#!/usr/bin/env python3
"""Candidate 05 v23: single-variable PBA trading-action ablation.

The position-building balance detector, sponsorship classification, three-close
acceptance hold, first-retest observation and all diagnostics remain active.
Only the final order-submission action is removed. This separates whether the
PBA scenario itself contributes post-cost alpha from any slot interactions it
causes with the established liquidity-reversal paths.

This is a diagnostic experiment, not a production strategy. If removal avoids
mature-flow losses but also removes an early-flow winner, the next candidate
must distinguish those auction phases causally rather than delete the whole
opportunity class or tune a fitted threshold.
"""
from __future__ import annotations

from typing import Any

from strategy_base import LiquidityResponseConfig
from strategy_v22 import ActualFillMilestoneStrategy


class PositionBuildingAcceptanceAblationStrategy(ActualFillMilestoneStrategy):
    """Observe PBA through confirmed retest, but never submit its entry."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics["ablation_pba_confirmed_retests_not_traded"] = 0

    def _submit_balance_acceptance(
        self,
        watch: Any,
        row: dict[str, float | int],
    ) -> bool:
        self.diagnostics["ablation_pba_confirmed_retests_not_traded"] += 1
        self._expire_balance_watch(
            row,
            "ABLATION_POSITION_BUILDING_BALANCE_ACCEPTANCE_ENTRY_REMOVED",
        )
        return False


__all__ = ["PositionBuildingAcceptanceAblationStrategy"]
