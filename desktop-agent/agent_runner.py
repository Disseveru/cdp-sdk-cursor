#!/usr/bin/env python3
"""CDP Flash Liquidation Agent — desktop runner with live dashboard."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agent.ai_engine import LiquidationAIEngine
from agent.cdp_wallet import CdpWalletManager
from agent.execution_history import ExecutionHistory, ExecutionRecord
from agent.executor import FlashLiquidationExecutor
from agent.flashblocks_client import FlashblocksSubscriber
from agent.liquidation_intel import enrich_volatility_context
from agent.macro_calendar import is_volatility_window, volatility_summary
from agent.oracle_monitor import OracleMonitor
from agent.scanner import MultiProtocolScanner
from agent.watch_staging import WatchStager
from config.settings import load_settings
from ui.dashboard import app, push_log, update_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agent_runner")

EXECUTABLE_PROTOCOLS = ("aave-v3", "morpho", "moonwell")


class FlashLiquidationAgent:
    def __init__(self, network: str | None = None, scan_only: bool = False) -> None:
        self.settings = load_settings(network)
        if scan_only:
            object.__setattr__(self.settings, "execute_enabled", False)
        self.wallet = CdpWalletManager(self.settings)
        self.scanner: MultiProtocolScanner | None = None
        self.executor: FlashLiquidationExecutor | None = None
        self.ai = LiquidationAIEngine(self.settings)
        self.oracle_monitor: OracleMonitor | None = None
        self.watch_stager: WatchStager | None = None
        self.flashblocks: FlashblocksSubscriber | None = None
        self._running = False
        self._oracle_trigger = asyncio.Event()
        self._fast_scan_trigger = asyncio.Event()
        self.history = ExecutionHistory()

    async def setup(self) -> None:
        bundle = await self.wallet.initialize()
        self.scanner = MultiProtocolScanner(self.settings, bundle.w3)
        self.executor = FlashLiquidationExecutor(self.settings, self.wallet)
        self.oracle_monitor = OracleMonitor(bundle.w3)
        self.watch_stager = WatchStager(self.settings, self.executor)
        self.flashblocks = FlashblocksSubscriber(self.settings)
        self.flashblocks.set_handlers(
            on_oracle_update=self._on_flashblocks_oracle,
            on_new_head=self._on_flashblocks_head,
        )

        update_state(
            status="ready",
            network=self.settings.network,
            smart_account=bundle.smart_account.address,
            enabled_protocols=self.scanner.protocol_names,
            execute_enabled=self.settings.execute_enabled,
            flash_liquidator=self.settings.flash_liquidator_address,
            morpho_liquidator=self.settings.morpho_flash_liquidator_address,
            moonwell_liquidator=self.settings.moonwell_flash_liquidator_address,
            moonwell_oev_liquidator=self.settings.moonwell_oev_flash_liquidator_address,
            execution_history=self.history.load()[:20],
            execution_summary=self.history.summary(),
            macro_mode="normal",
            staged_liquidations=0,
        )
        push_log(f"Agent ready on {self.settings.network}")
        push_log(f"Protocols: {', '.join(self.scanner.protocol_names)}")
        push_log(f"Smart account: {bundle.smart_account.address}")
        if self.settings.flash_liquidator_address:
            push_log(f"FlashLiquidator (Aave): {self.settings.flash_liquidator_address}")
        if self.settings.morpho_flash_liquidator_address:
            push_log(f"MorphoFlashLiquidator: {self.settings.morpho_flash_liquidator_address}")
        if self.settings.moonwell_flash_liquidator_address:
            push_log(f"MoonwellFlashLiquidator: {self.settings.moonwell_flash_liquidator_address}")
        if self.settings.moonwell_oev_flash_liquidator_address:
            push_log(f"MoonwellOEVFlashLiquidator: {self.settings.moonwell_oev_flash_liquidator_address}")
        if self.settings.flashblocks_enabled:
            push_log("Flashblocks fast-path: enabled")
        if self.settings.macro_calendar_enabled:
            push_log(f"Macro calendar: {volatility_summary()}")

        await self.flashblocks.start()

    async def _on_flashblocks_oracle(self, feed_name: str, _event: dict) -> None:
        if self.oracle_monitor is not None:
            self.oracle_monitor.note_external_update(feed_name)
        if self.settings.oracle_triggered_scan:
            self._oracle_trigger.set()
            push_log(f"Flashblocks oracle tick: {feed_name}")

    async def _on_flashblocks_head(self, block: int) -> None:
        self._fast_scan_trigger.set()
        update_state(latest_flashblock=block)

    def _macro_active(self) -> bool:
        if not self.settings.macro_calendar_enabled:
            return self.settings.volatility_mode
        return self.settings.volatility_mode or is_volatility_window()

    async def _try_staged_execution(self, macro_active: bool) -> bool:
        assert self.executor is not None
        assert self.watch_stager is not None
        for staged in self.watch_stager.list_fresh():
            target = staged.target
            min_profit = __import__(
                "agent.dynamic_profit", fromlist=["effective_min_profit_usd"]
            ).effective_min_profit_usd(self.settings, target.health_factor, macro_active=macro_active)
            if target.estimated_profit_usd < min_profit:
                continue
            push_log(f"Trying staged liquidation {target.protocol_id} {target.user}")
            result = await self.executor.execute(
                target,
                prebuilt_data=staged.calldata,
                prebuilt_contract=staged.contract,
            )
            if result.status not in ("simulation_failed", "failed"):
                record = self.history.append(ExecutionRecord.from_execution_result(result))
                update_state(
                    last_execution=result.to_dict(),
                    execution_history=self.history.load()[:20],
                    execution_summary=self.history.summary(),
                )
                push_log(result.message)
                return True
        return False

    async def _execute_decision(self, targets, decision, macro_active: bool) -> None:
        assert self.executor is not None
        if not (
            self.settings.execute_enabled
            and decision.action == "execute"
            and decision.target_user
            and decision.protocol_id in EXECUTABLE_PROTOCOLS
        ):
            return

        match = next(
            (
                t
                for t in targets
                if t.user.lower() == decision.target_user.lower()
                and t.protocol_id == decision.protocol_id
                and t.executable
            ),
            None,
        )
        if match is None:
            return

        contract_ok = (
            (match.protocol_id == "aave-v3" and self.settings.flash_liquidator_address)
            or (match.protocol_id == "morpho" and self.settings.morpho_flash_liquidator_address)
            or (
                match.protocol_id == "moonwell"
                and (
                    self.settings.moonwell_flash_liquidator_address
                    or (match.use_oev_path and self.settings.moonwell_oev_flash_liquidator_address)
                )
            )
        )
        if not contract_ok:
            return

        push_log(f"Executing {match.protocol_name} liquidation for {match.user}")
        result = await self.executor.execute(match)
        record = self.history.append(ExecutionRecord.from_execution_result(result))
        update_state(
            last_execution=result.to_dict(),
            execution_history=self.history.load()[:20],
            execution_summary=self.history.summary(),
        )
        push_log(result.message)
        if result.user_op_hash:
            push_log(f"UserOp: {result.user_op_hash}")
        if result.tx_hash:
            push_log(f"Tx: https://basescan.org/tx/{result.tx_hash}")

    async def _scan_cycle(self, *, force_fast: bool = False) -> float:
        assert self.scanner is not None
        assert self.watch_stager is not None

        macro_active = self._macro_active()
        update_state(
            macro_mode=volatility_summary() if macro_active else "normal",
            dynamic_min_profit=self.settings.min_profit_volatile_usd if macro_active else self.settings.min_profit_usd,
        )

        oracle_updates: list[str] = []
        if self.oracle_monitor is not None:
            oracle_updates = self.oracle_monitor.poll_feeds()
            if oracle_updates:
                push_log(f"Oracle updates: {', '.join(oracle_updates)} — priority rescan")
                force_fast = True

        if macro_active and not oracle_updates:
            push_log(f"Macro volatility window active: {volatility_summary()}")

        if macro_active or force_fast:
            intel = await enrich_volatility_context(self.settings)
            if intel.get("sources"):
                push_log(f"Liquidation intel: {', '.join(intel['sources'])}")
                update_state(liquidation_intel=intel)

        if force_fast and self.settings.execute_enabled:
            if await self._try_staged_execution(macro_active):
                return self.settings.fast_scan_interval_seconds

        borrowers_by_protocol = await self.scanner.discover_borrowers()
        watch_list = await self.scanner.fetch_watch_list()
        priority_users = [w.user for w in watch_list]
        total_borrowers = sum(len(v) for v in borrowers_by_protocol.values())
        push_log(
            f"Tracking {total_borrowers} borrowers ({self.scanner.borrower_stats(borrowers_by_protocol)})"
        )

        targets = await self.scanner.scan(
            borrowers_by_protocol,
            macro_active=macro_active,
            priority_users=priority_users if force_fast else None,
        )
        staged = await self.watch_stager.refresh_from_watch(
            watch_list,
            self.scanner.resolve_watch_target,
        )
        update_state(staged_liquidations=staged)

        decision = await self.ai.decide(targets, macro_active=macro_active)

        update_state(
            targets=[t.to_dict() for t in targets[:25]],
            watch_targets=[w.to_dict() for w in watch_list[:15]],
            decision=decision.to_dict(),
        )
        from ui.dashboard import _state

        _state["scan_count"] = _state.get("scan_count", 0) + 1

        push_log(
            f"Scan: {len(targets)} liquidatable, {len(watch_list)} watch, {staged} staged | "
            f"decision={decision.action}"
        )

        await self._execute_decision(targets, decision, macro_active)

        if macro_active or force_fast:
            return self.settings.fast_scan_interval_seconds
        return float(self.settings.scan_interval_seconds)

    async def run_loop(self) -> None:
        assert self.scanner is not None
        self._running = True
        update_state(status="scanning")

        while self._running:
            try:
                force_fast = False
                if self._oracle_trigger.is_set():
                    self._oracle_trigger.clear()
                    force_fast = True
                if self._fast_scan_trigger.is_set():
                    self._fast_scan_trigger.clear()
                    force_fast = True

                sleep_for = await self._scan_cycle(force_fast=force_fast)
            except Exception as exc:
                logger.exception("Agent loop error")
                push_log(f"Error: {exc}")
                sleep_for = float(self.settings.scan_interval_seconds)

            await self._wait_for_scan_trigger(sleep_for)

    async def _wait_for_scan_trigger(self, timeout: float) -> None:
        """Sleep until timeout or an oracle / Flashblocks head trigger fires."""
        if self._oracle_trigger.is_set() or self._fast_scan_trigger.is_set():
            return

        oracle_waiter = asyncio.create_task(self._oracle_trigger.wait())
        fast_waiter = asyncio.create_task(self._fast_scan_trigger.wait())
        try:
            _done, pending = await asyncio.wait(
                {oracle_waiter, fast_waiter},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        except asyncio.CancelledError:
            oracle_waiter.cancel()
            fast_waiter.cancel()
            raise

    async def shutdown(self) -> None:
        self._running = False
        if self.flashblocks is not None:
            await self.flashblocks.stop()
        await self.wallet.close()
        update_state(status="stopped")


async def main() -> None:
    parser = argparse.ArgumentParser(description="CDP Flash Liquidation Desktop Agent")
    parser.add_argument("--network", choices=["base", "base-sepolia"], default=None)
    parser.add_argument("--scan-only", action="store_true", help="Disable execution even if EXECUTE_ENABLED=true")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = load_settings(args.network)
    if args.port:
        object.__setattr__(settings, "dashboard_port", args.port)

    agent = FlashLiquidationAgent(args.network, scan_only=args.scan_only)
    await agent.setup()

    config = uvicorn.Config(
        app,
        host=agent.settings.dashboard_host,
        port=agent.settings.dashboard_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    async def run_agent() -> None:
        await agent.run_loop()

    try:
        await asyncio.gather(server.serve(), run_agent())
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
