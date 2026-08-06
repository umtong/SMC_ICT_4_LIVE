#!/usr/bin/env python3
"""Candidate 05 v26 diagnostic: remove only early sponsored CHoCH participation."""
from __future__ import annotations

from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_v13 import TargetLiquidityHandoffStrategy
from strategy_v26 import ScenarioValidEntryStrategy


class NoEarlySponsoredParticipationStrategy(ScenarioValidEntryStrategy):
    """Route every confirmed CHoCH through observation before any entry.

    This diagnostic removes only v17's early sponsored-CHoCH marketable
    participation. Sweep classification, tail-flow, directional depth,
    displacement, frozen target, confirmed retest/second-touch logic, PBA,
    scenario-valid order lifecycle, costs, slippage and 3% current-NAV sizing
    are unchanged.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics["early_sponsored_participation_ablated"] = 0

    def _submit_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        self.diagnostics["early_sponsored_participation_ablated"] += 1

        # Route through v13's observation path, but preserve v21's scenario
        # lifecycle: every armed CHoCH must freeze its original live opposing
        # liquidity destination before any later retest or second-touch logic.
        handled = TargetLiquidityHandoffStrategy._submit_entry(self, setup, row)
        armed = self.armed_entry_path
        if armed is None or armed.setup.scenario_id != setup.scenario_id:
            return handled
        if "frozen_target_price" in armed.details:
            return handled
        if self._freeze_scenario_destination(armed, row):
            return handled

        # Match v21: this CHoCH was consumed and explicitly closed because no
        # coherent live destination remained, so do not detect an overlapping
        # setup on the same completed bar.
        return True


__all__ = ["NoEarlySponsoredParticipationStrategy"]
