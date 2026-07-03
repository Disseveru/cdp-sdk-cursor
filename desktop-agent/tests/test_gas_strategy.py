"""Tests for gas bidding strategy."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.gas_strategy import compute_gas_bid


def test_compute_gas_bid_returns_positive_fees():
    w3 = MagicMock()
    w3.eth.fee_history.return_value = {
        "baseFeePerGas": [1_000_000_000],
        "reward": [[2_000_000], [3_000_000]],
    }
    bid = compute_gas_bid(w3, urgency="liquidation")
    assert bid.max_priority_fee_per_gas > 0
    assert bid.max_fee_per_gas >= bid.max_priority_fee_per_gas
    assert "maxPriorityFeePerGas" in bid.paymaster_context()
