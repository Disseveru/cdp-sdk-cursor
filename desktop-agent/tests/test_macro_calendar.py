"""Tests for macro calendar volatility windows."""

from __future__ import annotations

from datetime import UTC, datetime

from agent.macro_calendar import MacroEvent, active_macro_events, is_volatility_window


def test_macro_event_active_during_window():
    event = MacroEvent("Test CPI", datetime(2026, 7, 10).date(), window_hours=6)
    inside = datetime(2026, 7, 10, 14, 0, tzinfo=UTC)
    outside = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
    assert event.is_active(inside)
    assert not event.is_active(outside)


def test_july_nfp_in_calendar():
  events = active_macro_events(datetime(2026, 7, 3, 14, 0, tzinfo=UTC))
  assert any("NFP" in e.name for e in events)
