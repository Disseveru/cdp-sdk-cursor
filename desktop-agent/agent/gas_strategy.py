"""Dynamic EIP-1559 priority fee estimation for competitive liquidation inclusion."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from web3 import Web3

logger = logging.getLogger(__name__)

LIQUIDATION_PERCENTILE = 95
ARB_PERCENTILE = 75
DEFAULT_PRIORITY_WEI = 1_000_000  # 0.001 gwei
MIN_PRIORITY_WEI = 100_000
MAX_PRIORITY_WEI = 50_000_000_000  # 50 gwei cap


@dataclass(frozen=True)
class GasBid:
    max_priority_fee_per_gas: int
    max_fee_per_gas: int
    percentile: int
    source: str

    def paymaster_context(self) -> dict[str, str]:
        """ERC-7677 context forwarded to CDP paymaster for inclusion bidding."""
        return {
            "maxPriorityFeePerGas": hex(self.max_priority_fee_per_gas),
            "maxFeePerGas": hex(self.max_fee_per_gas),
        }


def _percentile(values: list[int], pct: int) -> int:
    if not values:
        return DEFAULT_PRIORITY_WEI
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(len(ordered) * pct / 100) - 1))
    return max(MIN_PRIORITY_WEI, ordered[idx])


def compute_gas_bid(
    w3: Web3,
    *,
    urgency: str = "liquidation",
    blocks: int = 10,
) -> GasBid:
    """Derive priority fee from recent block rewards (95th for liquidations)."""
    percentile = LIQUIDATION_PERCENTILE if urgency == "liquidation" else ARB_PERCENTILE
    try:
        history = w3.eth.fee_history(blocks, "latest", [percentile / 100.0])
        rewards = history.get("reward") or []
        priority_samples = [int(row[0]) for row in rewards if row]
        priority = _percentile(priority_samples, percentile) if priority_samples else DEFAULT_PRIORITY_WEI
        base_fee = int(history.get("baseFeePerGas", [w3.eth.gas_price])[-1])
        # Survive one base-fee bump across Flashblocks windows
        max_fee = base_fee * 2 + priority
        priority = min(MAX_PRIORITY_WEI, int(priority * 1.1))
        return GasBid(
            max_priority_fee_per_gas=priority,
            max_fee_per_gas=max_fee,
            percentile=percentile,
            source="fee_history",
        )
    except Exception as exc:
        logger.debug("fee_history fallback: %s", exc)
        try:
            priority = max(MIN_PRIORITY_WEI, int(w3.eth.max_priority_fee))
            base = int(w3.eth.gas_price)
            return GasBid(
                max_priority_fee_per_gas=min(MAX_PRIORITY_WEI, priority),
                max_fee_per_gas=base + priority,
                percentile=percentile,
                source="eth_max_priority_fee",
            )
        except Exception:
            return GasBid(
                max_priority_fee_per_gas=DEFAULT_PRIORITY_WEI,
                max_fee_per_gas=DEFAULT_PRIORITY_WEI * 3,
                percentile=percentile,
                source="default",
            )
