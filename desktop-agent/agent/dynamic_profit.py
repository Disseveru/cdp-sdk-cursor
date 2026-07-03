"""Dynamic minimum-profit thresholds based on health factor and market volatility."""

from __future__ import annotations

from config.settings import AgentSettings

# Health-factor tiers for dynamic minimum-profit scaling.
HF_TIER_URGENT = 0.95
HF_TIER_ELEVATED = 0.98
HF_TIER_NEAR = 1.0
HF_MULTIPLIER_URGENT = 0.4
HF_MULTIPLIER_ELEVATED = 0.65
HF_MULTIPLIER_NEAR = 0.85


def is_volatility_mode(settings: AgentSettings, *, macro_active: bool = False) -> bool:
    """True when macro calendar or explicit volatility flag lowers profit bar."""
    if not settings.dynamic_profit_enabled:
        return False
    return macro_active or settings.volatility_mode


def effective_min_profit_usd(
    settings: AgentSettings,
    health_factor: float,
    *,
    macro_active: bool = False,
) -> float:
    """Scale MIN_PROFIT_USD down for urgent liquidations and volatility windows."""
    if not settings.dynamic_profit_enabled:
        return settings.min_profit_usd

    base = settings.min_profit_volatile_usd if is_volatility_mode(settings, macro_active=macro_active) else settings.min_profit_usd
    floor = settings.min_profit_floor_usd

    if health_factor <= 0:
        return floor
    if health_factor < HF_TIER_URGENT:
        return max(floor, base * HF_MULTIPLIER_URGENT)
    if health_factor < HF_TIER_ELEVATED:
        return max(floor, base * HF_MULTIPLIER_ELEVATED)
    if health_factor < HF_TIER_NEAR:
        return max(floor, base * HF_MULTIPLIER_NEAR)
    return base
