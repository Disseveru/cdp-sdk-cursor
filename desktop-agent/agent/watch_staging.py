"""Pre-staged liquidation calldata for near-liquidation watch targets."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from agent.executor import FlashLiquidationExecutor
from agent.models import LiquidationTarget, WatchTarget
from config.settings import AgentSettings

logger = logging.getLogger(__name__)

STAGE_TTL_SECONDS = 90


@dataclass
class StagedLiquidation:
    target: LiquidationTarget
    contract: str
    calldata: str
    built_at: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.built_at

    def is_fresh(self, ttl: float = STAGE_TTL_SECONDS) -> bool:
        return self.age_seconds <= ttl

    def cache_key(self) -> str:
        return f"{self.target.protocol_id}:{self.target.user.lower()}"


class WatchStager:
    """Build and cache liquidation calldata for HF 1.0–watch_threshold positions."""

    def __init__(self, settings: AgentSettings, executor: FlashLiquidationExecutor) -> None:
        self.settings = settings
        self.executor = executor
        self._cache: dict[str, StagedLiquidation] = {}

    def get(self, protocol_id: str, user: str) -> StagedLiquidation | None:
        key = f"{protocol_id}:{user.lower()}"
        staged = self._cache.get(key)
        if staged is None or not staged.is_fresh():
            self._cache.pop(key, None)
            return None
        return staged

    def stage_target(self, target: LiquidationTarget) -> StagedLiquidation | None:
        if not target.executable:
            return None
        try:
            calldata = self.executor.encode_liquidate_call(target)
            contract = self.executor._contract_for(target)
            staged = StagedLiquidation(target=target, contract=contract, calldata=calldata)
            self._cache[staged.cache_key()] = staged
            return staged
        except Exception as exc:
            logger.debug("Stage failed %s %s: %s", target.protocol_id, target.user, exc)
            return None

    async def refresh_from_watch(
        self,
        watch_targets: list[WatchTarget],
        resolve_target,
    ) -> int:
        """Resolve watch entries to full targets and pre-build calldata. Returns count staged."""
        staged_count = 0
        for watch in watch_targets[:25]:
            if watch.health_factor >= self.settings.watch_hf_threshold:
                continue
            try:
                target = await resolve_target(watch)
                if target is None or not target.executable:
                    continue
                if self.stage_target(target):
                    staged_count += 1
            except Exception as exc:
                logger.debug("Watch resolve %s: %s", watch.user, exc)
        return staged_count

    def prune_stale(self) -> None:
        stale = [k for k, v in self._cache.items() if not v.is_fresh()]
        for key in stale:
            del self._cache[key]

    @property
    def staged_count(self) -> int:
        self.prune_stale()
        return len(self._cache)

    def list_fresh(self) -> list[StagedLiquidation]:
        self.prune_stale()
        return list(self._cache.values())
