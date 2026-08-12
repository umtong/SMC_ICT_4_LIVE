"""Finite-history performance wrapper for the public ichiV2 adapter.

One thousand completed one-minute bars cover more than eight EMA96 half-lives
and all exact Ichimoku/rolling state used by the five-minute source. Decision
rules, source parameters, risk geometry and management are unchanged.
"""
from __future__ import annotations

from collections import deque

from strategy_base import SYMBOLS
from strategy_ichi_v2_base import (
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
                "candidate57_ichi_v2_finite_history_v2": 1,
                "indicator_history_max_completed_minutes": 1000,
                "alpha_rule_changed_by_optimization": 0,
            }
        )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
