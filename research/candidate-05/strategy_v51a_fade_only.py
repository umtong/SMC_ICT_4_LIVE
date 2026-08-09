"""Candidate 05 v51a: remove only rolling-value trend continuation."""
from __future__ import annotations

from typing import Any

from strategy_v51_rolling_value import RollingValueAuctionStrategy,RollingValueState


class RollingValueFadeOnlyStrategy(RollingValueAuctionStrategy):
    def _directional(self,row:dict[str,float|int],state:RollingValueState)->None:
        self.diagnostics['v51_directional_windows']+=0
        return


CandidateStrategy=RollingValueFadeOnlyStrategy
StrategyClass=RollingValueFadeOnlyStrategy
