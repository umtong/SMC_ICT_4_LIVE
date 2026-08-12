"""Finite-state performance wrapper for the private ichi5m reconstruction.

The source footprint needs at most EMA96 plus a 52/26 Ichimoku history. Keeping
the latest 512 completed one-minute bars leaves over four EMA96 half-lives and
all exact rolling-window state, while avoiding repeated work over 6,000 bars.
No decision rule, parameter, risk geometry or management rule changes.
"""
from __future__ import annotations

from collections import deque

from strategy_base import SYMBOLS
from strategy_ichi5m_private_base import (
    Candidate35Config as Candidate35Config,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Strategy(_BaseStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=512)
            for symbol in SYMBOLS
        }
        self.diagnostics.update(
            {
                "candidate57_finite_history_optimization_v2": 1,
                "indicator_history_max_completed_minutes": 512,
                "alpha_rule_changed_by_optimization": 0,
            }
        )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
