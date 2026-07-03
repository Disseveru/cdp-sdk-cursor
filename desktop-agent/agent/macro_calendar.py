"""Macro event calendar — widen scanning during high-volatility windows (2026 schedule)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta


@dataclass(frozen=True)
class MacroEvent:
    name: str
    event_date: date
    window_hours: int = 6

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        start = datetime(
            self.event_date.year,
            self.event_date.month,
            self.event_date.day,
            12,
            0,
            tzinfo=UTC,
        ) - timedelta(hours=self.window_hours)
        end = start + timedelta(hours=self.window_hours * 2 + 12)
        return start <= now <= end


# Recurring 2026 macro dates (US CPI ~10th–13th, NFP first Friday, FOMC decision days).
_MACRO_EVENTS_2026: list[MacroEvent] = [
    MacroEvent("US CPI", date(2026, 1, 13)),
    MacroEvent("US NFP", date(2026, 1, 9)),
    MacroEvent("FOMC", date(2026, 1, 28)),
    MacroEvent("US CPI", date(2026, 2, 11)),
    MacroEvent("US NFP", date(2026, 2, 6)),
    MacroEvent("ETH/BTC selloff", date(2026, 2, 5), window_hours=48),
    MacroEvent("US CPI", date(2026, 3, 11)),
    MacroEvent("US NFP", date(2026, 3, 6)),
    MacroEvent("FOMC", date(2026, 3, 18)),
    MacroEvent("US CPI", date(2026, 4, 10)),
    MacroEvent("US NFP", date(2026, 4, 3)),
    MacroEvent("KelpDAO stress", date(2026, 4, 14), window_hours=72),
    MacroEvent("US CPI", date(2026, 5, 12)),
    MacroEvent("US NFP", date(2026, 5, 8)),
    MacroEvent("FOMC", date(2026, 5, 6)),
    MacroEvent("US CPI", date(2026, 6, 10)),
    MacroEvent("US NFP", date(2026, 6, 5), window_hours=12),
    MacroEvent("BTC cascade", date(2026, 6, 17), window_hours=72),
    MacroEvent("US CPI", date(2026, 7, 10)),
    MacroEvent("US NFP", date(2026, 7, 3)),
    MacroEvent("FOMC", date(2026, 7, 29)),
    MacroEvent("US CPI", date(2026, 8, 12)),
    MacroEvent("US NFP", date(2026, 8, 7)),
    MacroEvent("US CPI", date(2026, 9, 10)),
    MacroEvent("US NFP", date(2026, 9, 4)),
    MacroEvent("FOMC", date(2026, 9, 16)),
    MacroEvent("US CPI", date(2026, 10, 13)),
    MacroEvent("US NFP", date(2026, 10, 2)),
    MacroEvent("US CPI", date(2026, 11, 12)),
    MacroEvent("US NFP", date(2026, 11, 6)),
    MacroEvent("FOMC", date(2026, 11, 4)),
    MacroEvent("US CPI", date(2026, 12, 10)),
    MacroEvent("US NFP", date(2026, 12, 4)),
    MacroEvent("FOMC", date(2026, 12, 16)),
]


def active_macro_events(now: datetime | None = None) -> list[MacroEvent]:
    """Return macro events whose volatility window is active right now."""
    now = now or datetime.now(UTC)
    return [e for e in _MACRO_EVENTS_2026 if e.is_active(now)]


def is_volatility_window(now: datetime | None = None) -> bool:
    return bool(active_macro_events(now))


def volatility_summary(now: datetime | None = None) -> str:
    events = active_macro_events(now)
    if not events:
        return "normal"
    return ", ".join(e.name for e in events)
