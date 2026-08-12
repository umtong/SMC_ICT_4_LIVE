"""Warmup/runoff-safe execution wrapper for Candidate 60.

The inherited Zaratustra strategy remains authoritative for entries, one-slot
execution, current-NAV 3% sizing, brackets, trailing, costs and accounting.  This
wrapper changes no trading rule.  It only retains enough causal history for the
100-hour RAHTF drift state and prevents new entries outside the declared scored
signal window while still allowing an existing position to finish in runoff.
"""
from __future__ import annotations

from collections import deque

from strategy_base import SYMBOLS
from strategy_zaratustra_base import (
    Candidate35Config as _ZaratustraConfig,
    Candidate35Strategy as _ZaratustraStrategy,
)


class Candidate35Config(_ZaratustraConfig, frozen=True):
    c60_signal_start_ns: int = 0
    c60_signal_end_ns: int = 9_223_372_036_854_775_807
    c60_history_minutes: int = 16_000


class Candidate35Strategy(_ZaratustraStrategy):
    """Source-identical Zaratustra execution with causal state-history support."""

    def __init__(self, config: Candidate35Config) -> None:
        if int(config.c60_signal_start_ns) > int(config.c60_signal_end_ns):
            raise ValueError("c60 signal window is inverted")
        if int(config.c60_history_minutes) < 8_000:
            raise ValueError("c60 history must cover the frozen RAHTF state")
        super().__init__(config)
        history = int(config.c60_history_minutes)
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=history)
            for symbol in SYMBOLS
        }
        self.diagnostics.update(
            {
                "candidate60_zara_rahtf_v1": 1,
                "c60_signal_start_ns": int(config.c60_signal_start_ns),
                "c60_signal_end_ns": int(config.c60_signal_end_ns),
                "c60_history_minutes": history,
                "c60_outside_signal_minutes": 0,
                "c60_policy_changed_execution_or_risk": 0,
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        in_signal_window = (
            int(self.config.c60_signal_start_ns)
            <= int(ts_event)
            <= int(self.config.c60_signal_end_ns)
        )
        if not open_symbols and not self.entry_pending and not in_signal_window:
            self.minute_index += 1
            self.diagnostics["complete_universe_minutes"] += 1
            self._record_equity(ts_event)
            self.diagnostics["c60_outside_signal_minutes"] += 1
            return
        super()._on_complete_universe_minute(ts_event)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-60-zara-rahtf-v1",
                    "c60_rahtf_mode": __import__("router").state_mode(),
                    "c60_signal_window_enforced": 1,
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
