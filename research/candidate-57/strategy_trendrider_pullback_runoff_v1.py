"""Implementation-only end-flat wrapper for the frozen TrendRider policy.

The alpha policy is inherited unchanged.  This wrapper separates the last
permitted entry timestamp from the data replay end so a position opened near the
boundary can complete its already-frozen lifecycle without allowing new runoff
entries.
"""
from __future__ import annotations

from strategy_base import SYMBOLS
from strategy_trendrider_base import (
    Candidate35Config as _BaseConfig,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Config(_BaseConfig, frozen=True):
    trendrider_signal_end_ns: int = 0


class Candidate35Strategy(_BaseStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "trendrider_runoff_wrapper": 1,
                "trendrider_signal_end_ns": int(config.trendrider_signal_end_ns),
                "trendrider_post_cutoff_flat_minutes": 0,
                "trendrider_alpha_policy_changed_for_runoff": 0,
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        cutoff = int(self.config.trendrider_signal_end_ns)
        if cutoff <= 0 or int(ts_event) <= cutoff:
            super()._on_complete_universe_minute(ts_event)
            return

        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        # The parent handles all existing orders and positions, including the
        # unchanged source lifecycle and the global-slot safety checks.  It
        # returns before routing a new signal whenever either state exists.
        if open_symbols or self.entry_pending:
            super()._on_complete_universe_minute(ts_event)
            return

        # Flat after the frozen entry cutoff: retain the continuous equity clock
        # but do not call the parent's route/submit path.
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]), 0
        )
        self.diagnostics["trendrider_post_cutoff_flat_minutes"] += 1


__all__ = ["Candidate35Config", "Candidate35Strategy"]
