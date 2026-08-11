"""Fast event-driven diagnostic for candidate-easychart v3.

The implementation is split by responsibility so each component remains
auditable: pending setup/first-retest lifecycle, position accounting, shared
types, and metrics.  This remains non-authoritative; NautilusTrader promotion
is required for accepted performance evidence.
"""
from __future__ import annotations

from dataclasses import asdict

from simulator_v3_pending import PendingEngineMixin
from simulator_v3_position import PositionEngineMixin
from simulator_v3_types import InstrumentSpec, MinuteBar, TradeRecord


class ContinuousAccountSimulator(PendingEngineMixin, PositionEngineMixin):
    def metrics(self, calendar_days: int) -> dict[str, object]:
        from simulator_v3_metrics import calculate_metrics
        return calculate_metrics(self, calendar_days)

    def trade_rows(self) -> list[dict[str, object]]:
        return [asdict(trade) for trade in self.trades]


__all__ = [
    "ContinuousAccountSimulator",
    "InstrumentSpec",
    "MinuteBar",
    "TradeRecord",
]
