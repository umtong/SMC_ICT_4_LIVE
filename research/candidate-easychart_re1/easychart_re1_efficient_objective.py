"""Efficient causal one-minute first-obstacle objective for EasyChart RE1.

The first fine-objective experiment represented one-minute objectives with the
full structure book.  That book unnecessarily built every one-minute trend line
and channel and timed out before producing useful evidence.  The objective
problem only needs confirmed horizontal reaction pivots and their first-touch
lifecycle.

This module reuses the existing causal pivot confirmation and target semantics,
but its one-minute book deliberately does not construct diagonal structures.
Immediately before entry, the nearest still-unspent confirmed 1m opposing pivot
may replace a farther 5m/15m objective.  If that true first obstacle leaves less
than the existing 1.0 gross R minimum, the plan is rejected rather than skipping
past it to manufacture a larger reward/risk ratio.

Channel rotations keep their explicit channel objective.  Channel-edge
reversals remain diagnostic-only in the quality core.  There is no fitted R cap,
outcome information, partial exit, target movement or post-entry management.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Candle
from easychart_re1_channel_abstention import (
    ChannelAbstainingMicroEngine,
    EasyChartRE1ChannelAbstentionBundle,
)
from easychart_re1_fine_objective import (
    FIRST_MICRO_OBSTACLE_RULE,
    FirstMicroObstacleMixin,
)
from easychart_re1_flow_ob_responsibility import (
    ResponsibleFlowValidatedDecisionAreaEngine,
)
from easychart_re1_flow_ob_sweep_responsibility import (
    ResponsibleFlowMajorSwingEngine,
)
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


PIVOT_ONLY_OBJECTIVE_BOOK_RULE = (
    "RESEARCH_IMPLEMENTATION:"
    "ONE_MINUTE_OBJECTIVE_BOOK_BUILDS_ONLY_CAUSALLY_CONFIRMED_HORIZONTAL_PIVOTS_AND_THEIR_FIRST_TOUCH_LIFECYCLE"
)
if PIVOT_ONLY_OBJECTIVE_BOOK_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (PIVOT_ONLY_OBJECTIVE_BOOK_RULE,)


class PivotOnlyObjectiveBook(NearestAnyPivotStructureBook):
    """Causal pivot book without irrelevant 1m line/channel construction."""

    def on_bar(self, bar: Candle):  # type: ignore[no-untyped-def]
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("objective bars must arrive in increasing close time")
        self.bars.append(bar)
        observed_index = len(self.bars) - 1
        pivots = self._register_pivots(observed_index)
        return pivots, [], []


class EfficientFirstMicroObstacleMixin(FirstMicroObstacleMixin):
    """Use the same first-obstacle policy with a pivot-only data structure."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entry_micro_structure = PivotOnlyObjectiveBook(
            self.symbol,
            self.trigger_minutes,
            self.tick_size,
            pivot_spans=(2, 6),
        )

    @property
    def efficient_micro_objective_diagnostics(self) -> dict[str, Any]:
        return {
            "objective": self.micro_objective_diagnostics,
            "book": "CAUSAL_PIVOT_ONLY",
            "rules": (
                FIRST_MICRO_OBSTACLE_RULE,
                PIVOT_ONLY_OBJECTIVE_BOOK_RULE,
            ),
        }


class EfficientObjectiveMicroEngine(
    EfficientFirstMicroObstacleMixin,
    ChannelAbstainingMicroEngine,
):
    pass


class EfficientObjectiveMajorSwingEngine(
    EfficientFirstMicroObstacleMixin,
    ResponsibleFlowMajorSwingEngine,
):
    pass


class EfficientObjectiveDecisionOBEngine(
    EfficientFirstMicroObstacleMixin,
    ResponsibleFlowValidatedDecisionAreaEngine,
):
    pass


class EasyChartRE1EfficientObjectiveBundle(EasyChartRE1ChannelAbstentionBundle):
    """Channel-abstaining quality core with the first causal 1m obstacle."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = EfficientObjectiveMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = EfficientObjectiveMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = EfficientObjectiveDecisionOBEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "major_swing", "flow_decision_ob"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["efficient_first_micro_obstacle"] = {
            "micro": self.micro.efficient_micro_objective_diagnostics,
            "major_swing": self.major_swing.efficient_micro_objective_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.efficient_micro_objective_diagnostics,
            "rules": (
                FIRST_MICRO_OBSTACLE_RULE,
                PIVOT_ONLY_OBJECTIVE_BOOK_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1EfficientObjectiveBundle
