"""Implementation-only warmup/end-flat wrapper for frozen TrendRider policy.

The alpha policy is inherited unchanged.  This wrapper separates the first and
last permitted entry timestamps from the data replay boundaries so the public
210-candle startup is unscored and a late position can complete its already-
frozen lifecycle without allowing runoff entries.
"""
from __future__ import annotations

from strategy_base import SYMBOLS
from strategy_trendrider_base import (
    Candidate35Config as _BaseConfig,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Config(_BaseConfig, frozen=True):
    trendrider_signal_start_ns: int = 0
    trendrider_signal_end_ns: int = 0


class Candidate35Strategy(_BaseStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "trendrider_runoff_wrapper": 1,
                "trendrider_signal_start_ns": int(config.trendrider_signal_start_ns),
                "trendrider_signal_end_ns": int(config.trendrider_signal_end_ns),
                "trendrider_pre_start_flat_minutes": 0,
                "trendrider_post_cutoff_flat_minutes": 0,
                "trendrider_alpha_policy_changed_for_runoff": 0,
            }
        )

    def _flat_clock_without_routing(self, ts_event: int, diagnostics_key: str) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]), 0
        )
        self.diagnostics[diagnostics_key] += 1

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        start = int(self.config.trendrider_signal_start_ns)
        end = int(self.config.trendrider_signal_end_ns)
        current = int(ts_event)

        # During unscored startup, bars and indicators continue to accumulate,
        # but the route/submit path is not called.
        if start > 0 and current < start:
            open_symbols = [
                symbol
                for symbol in SYMBOLS
                if not self.portfolio.is_flat(self.instrument_ids[symbol])
            ]
            if open_symbols or self.entry_pending:
                raise RuntimeError("TrendRider position/order exists before signal start")
            self._flat_clock_without_routing(
                ts_event, "trendrider_pre_start_flat_minutes"
            )
            return

        if end <= 0 or current <= end:
            super()._on_complete_universe_minute(ts_event)
            return

        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        # The parent handles all existing orders and positions, including the
        # unchanged source lifecycle and global-slot safety checks.  It returns
        # before routing a new signal whenever either state exists.
        if open_symbols or self.entry_pending:
            super()._on_complete_universe_minute(ts_event)
            return

        self._flat_clock_without_routing(
            ts_event, "trendrider_post_cutoff_flat_minutes"
        )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
