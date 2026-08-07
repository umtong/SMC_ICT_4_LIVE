#!/usr/bin/env python3
"""Candidate 05 v17: participate only in early coherent sponsored CHoCH."""
from __future__ import annotations

from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_v13 import TargetLiquidityHandoffStrategy
from strategy_v14 import SponsoredChochParticipationStrategy
from strategy_v16 import PositionBuildingBalanceAcceptanceStrategy
from sponsored_choch_logic import sponsored_choch_flow_phase_ready


class EarlySponsoredChochStrategy(PositionBuildingBalanceAcceptanceStrategy):
    """Separate early transition participation from mature path observation.

    The v16 strategy remains unchanged except for how an otherwise confirmed
    reversal CHoCH enters the market:

    * mirrored three-minute aggressor flow in [0, 1/3) is early and coherent;
      it may use v14/v15's bounded, cost-aware sponsored-CHoCH participation;
    * negative mirrored three-minute flow has not propagated through the wider
      auction, while >=1/3 already represents at least a 2:1 mature aggressor
      imbalance; both route through v13/v12/v9's one-minute retrace-or-
      breakaway observation instead of entering immediately.

    All sweep, tail-flow, depth, displacement, target, stop, execution, cost,
    risk, handoff and balance-acceptance rules are otherwise identical to v16.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "early_sponsored_choch_routed_to_participation": 0,
                "opposing_or_mature_choch_routed_to_observation": 0,
            },
        )

    def _early_sponsored_participation_allowed(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        flow_3m: float,
    ) -> bool:
        return sponsored_choch_flow_phase_ready(
            side=setup.side,
            flow_3m=flow_3m,
        )

    def _submit_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        flow_3m = self._feature("flow_3m")
        if self._early_sponsored_participation_allowed(
            setup,
            row,
            flow_3m,
        ):
            self.diagnostics["early_sponsored_choch_routed_to_participation"] += 1
            return SponsoredChochParticipationStrategy._submit_entry(self, setup, row)

        self.diagnostics["opposing_or_mature_choch_routed_to_observation"] += 1
        return TargetLiquidityHandoffStrategy._submit_entry(self, setup, row)


__all__ = ["EarlySponsoredChochStrategy"]
