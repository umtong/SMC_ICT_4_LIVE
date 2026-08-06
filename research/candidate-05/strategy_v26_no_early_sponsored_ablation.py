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
        return TargetLiquidityHandoffStrategy._submit_entry(self, setup, row)


__all__ = ["NoEarlySponsoredParticipationStrategy"]
