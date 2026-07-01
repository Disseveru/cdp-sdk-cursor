"""Arbitrage opportunity data model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ArbitrageOpportunity:
    loan_token: str
    intermediate_token: str
    loan_symbol: str
    intermediate_symbol: str
    amount_in: int
    expected_profit_raw: int
    expected_profit_usd: float
    leg1_router: str
    leg1_calldata: str
    leg2_router: str
    leg2_calldata: str
    leg1_amount_out: int = 0
    leg2_amount_out: int = 0
    route_label: str = ""

    @property
    def profit_bps(self) -> float:
        return (self.expected_profit_raw / self.amount_in) * 10_000 if self.amount_in else 0
