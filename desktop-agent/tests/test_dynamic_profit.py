"""Tests for dynamic profit thresholds."""

from __future__ import annotations

from agent.dynamic_profit import effective_min_profit_usd, is_volatility_mode
from tests.conftest import settings as settings_fixture


def test_effective_min_profit_lower_for_urgent_hf(settings):
    urgent = effective_min_profit_usd(settings, 0.92, macro_active=False)
    borderline = effective_min_profit_usd(settings, 0.99, macro_active=False)
    assert urgent < borderline
    assert urgent >= settings.min_profit_floor_usd


def test_volatility_mode_lowers_base(settings):
    calm = effective_min_profit_usd(settings, 0.97, macro_active=False)
    volatile = effective_min_profit_usd(settings, 0.97, macro_active=True)
    assert volatile <= calm


def test_is_volatility_mode_respects_flag(settings):
    assert not is_volatility_mode(settings, macro_active=False)
    assert is_volatility_mode(settings, macro_active=True)
