"""Finite-history wrapper for the frozen MBE collision-topology experiment.

Two hundred completed 5m candles (1,000 one-minute bars) cover the 140-candle
source startup, RSI14, TEMA9 and SMA20 entry state. EMA24/EMA96 values beyond
that window are diagnostics only. The workflow must first reproduce the
committed full-history April control before any fresh topology result is used.
"""
from __future__ import annotations

from collections import deque

import router
from strategy_base import SYMBOLS
from strategy_ichi_mbe_base import (
    Candidate35Config as Candidate35Config,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Strategy(_BaseStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=1_000)
            for symbol in SYMBOLS
        }
        self.diagnostics.update(
            {
                "candidate57_mbe_topology_finite_history_v1": 1,
                "indicator_history_max_completed_minutes": 1000,
                "mbe_topology_mode": router.mbe_topology_mode(),
                "mbe_source_entry_rule_changed": 0,
                "mbe_source_management_changed": 0,
                "mbe_outcome_filter_used": 0,
            }
        )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
