"""Session-aware diagnostic wrapper with causal setup expiry."""
from __future__ import annotations

from typing import Mapping

from simulator_v3 import ContinuousAccountSimulator, InstrumentSpec, MinuteBar, TradeRecord


class ExpiringContinuousAccountSimulator(ContinuousAccountSimulator):
    """Remove locally monitored entry intents when their causal window ends."""

    def on_timestamp(self, bars: Mapping[str, MinuteBar]) -> None:
        if bars:
            earliest_open = min(bar.ts_open_ns for bar in bars.values())
            for setup_id, pending in list(self.pending.items()):
                valid_until = getattr(pending.setup, "valid_until_ns", None)
                if valid_until is not None and int(valid_until) <= earliest_open:
                    self.pending.pop(setup_id, None)
                    self.diagnostics["setup_window_expired"] += 1
                    self.diagnostics[f"setup_window_expired_{pending.setup.family}"] += 1
        super().on_timestamp(bars)


__all__ = [
    "ExpiringContinuousAccountSimulator",
    "InstrumentSpec",
    "MinuteBar",
    "TradeRecord",
]
