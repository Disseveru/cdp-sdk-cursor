"""Tests for liquidation AI decision guards and threshold filtering."""

from __future__ import annotations

import pytest

from agent.ai_engine import AgentDecision, LiquidationAIEngine
from agent.models import LiquidationTarget
from config.settings import AgentSettings


def _target(
    *,
    user: str,
    profit: float,
    executable: bool = True,
    health_factor: float = 0.95,
    protocol_id: str = "aave-v3",
) -> LiquidationTarget:
    return LiquidationTarget(
        protocol_id=protocol_id,
        protocol_name="Aave V3" if protocol_id == "aave-v3" else protocol_id,
        user=user,
        health_factor=health_factor,
        total_collateral_usd=10_000.0,
        total_debt_usd=5_000.0,
        collateral_asset="0x4200000000000000000000000000000000000006",
        collateral_symbol="WETH",
        debt_asset="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        debt_symbol="USDC",
        debt_to_cover=1_000_000_000,
        debt_to_cover_human=1000.0,
        estimated_profit_usd=profit,
        swap_fee=500,
        flash_amount=1_000_000_000,
        liquidation_bonus_bps=500,
        executable=executable,
        debt_decimals=6,
        collateral_decimals=18,
    )


@pytest.mark.asyncio
async def test_rules_watch_when_executable_but_below_threshold(settings: AgentSettings):
    engine = LiquidationAIEngine(settings)
    targets = [_target(user=settings.smart_account_address, profit=2.0)]

    decision = await engine.decide(targets)

    assert decision.action == "watch"
    assert "below dynamic minimum" in decision.reasoning
    assert "below_profit_threshold" in decision.risk_flags


@pytest.mark.asyncio
async def test_rules_execute_when_above_threshold(settings: AgentSettings, aave_target: LiquidationTarget):
    engine = LiquidationAIEngine(settings)

    decision = await engine.decide([aave_target])

    assert decision.action == "execute"
    assert decision.target_user == aave_target.user


def test_guard_execute_rejects_below_threshold(settings: AgentSettings):
    engine = LiquidationAIEngine(settings)
    target = _target(user=settings.smart_account_address, profit=2.0)
    llm_decision = AgentDecision(
        action="execute",
        target_user=target.user,
        protocol_id=target.protocol_id,
        reasoning="LLM says go",
        risk_flags=[],
        recommended_gas_strategy="cdp_paymaster",
        source="openai",
    )

    guarded = engine._guard_execute_decision(llm_decision, [target], macro_active=False)

    assert guarded is None


def test_guard_execute_allows_above_threshold(settings: AgentSettings, aave_target: LiquidationTarget):
    engine = LiquidationAIEngine(settings)
    llm_decision = AgentDecision(
        action="execute",
        target_user=aave_target.user,
        protocol_id=aave_target.protocol_id,
        reasoning="LLM says go",
        risk_flags=[],
        recommended_gas_strategy="cdp_paymaster",
        source="openai",
    )

    guarded = engine._guard_execute_decision(llm_decision, [aave_target], macro_active=False)

    assert guarded is not None
    assert guarded.action == "execute"


def test_guard_non_execute_passes_through(settings: AgentSettings, aave_target: LiquidationTarget):
    engine = LiquidationAIEngine(settings)
    llm_decision = AgentDecision(
        action="watch",
        target_user=aave_target.user,
        protocol_id=aave_target.protocol_id,
        reasoning="LLM says wait",
        risk_flags=[],
        recommended_gas_strategy="cdp_paymaster",
        source="openai",
    )

    guarded = engine._guard_execute_decision(llm_decision, [aave_target], macro_active=False)

    assert guarded is not None
    assert guarded.action == "watch"
