"""Nautilus strategy integration for true one-minute MSS close plans."""
from __future__ import annotations

from model_structural_mss_close import TargetSafeStructuralMSSCloseRouter
from strategy_structural_mss import Candidate07Strategy as _RetestStrategy
from strategy import Candidate07StrategyConfig


class Candidate07Strategy(_RetestStrategy):
    """Keep structural source ownership but enter at completed true MSS close."""

    def __init__(self, config: Candidate07StrategyConfig):
        super().__init__(config)
        self.router = TargetSafeStructuralMSSCloseRouter(self.logic)


__all__ = ["Candidate07Strategy", "Candidate07StrategyConfig"]
