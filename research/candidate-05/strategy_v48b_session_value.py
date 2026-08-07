"""Candidate 05 v48b: preserve the prior session-value z state."""
from __future__ import annotations

import math
from typing import Any

from strategy_v48_session_value import SessionValue, SessionValueAuctionStrategy


class CorrectedSessionValueAuctionStrategy(SessionValueAuctionStrategy):
    """Require a real prior >1 sigma excursion before the first value pullback."""

    def __init__(self,config:Any)->None:
        super().__init__(config)
        self.v48b_prior_z_for_decision=math.nan
        self.diagnostics.update({'v48b_prior_excursion_confirmed':0})

    def on_bar(self,bar)->None:
        self.v48b_prior_z_for_decision=self.v48_previous_z
        super().on_bar(bar)

    def _directional_pullback(self,row:dict[str,float|int],state:SessionValue,key:int)->None:
        previous=self.v48b_prior_z_for_decision
        side=state.path_direction
        if not math.isfinite(previous) or side==0:
            return
        if side*previous<=1.0 or side*state.z<0.25 or side*state.z>1.0 or abs(state.z)>=abs(previous):
            return
        self.diagnostics['v48b_prior_excursion_confirmed']+=1
        super()._directional_pullback(row,state,key)


CandidateStrategy=CorrectedSessionValueAuctionStrategy
StrategyClass=CorrectedSessionValueAuctionStrategy
