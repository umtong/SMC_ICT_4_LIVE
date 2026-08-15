"""First significant one-minute opposing swing as the day-trade objective.

Every tiny one-minute pivot is not a meaningful obstacle.  The source repeatedly
uses an important prior high/low or opposing structure, and the existing RE1
translation already assigns span 6 to the larger local auction while span 2 is
the smallest reaction.  Using both spans for the objective made ordinary noise
block otherwise valid trades and rejected strong winners with less than 1R to a
minor pivot.

This candidate keeps the efficient pivot-only implementation but admits only a
causally confirmed span-6 one-minute swing as a refinement of the inherited
5m/15m objective.  Such a pivot needs six completed bars on each side before it
exists, yet can become visible earlier than a five-minute swing.  The nearest
still-unspent opposing span-6 swing available before entry is the immutable
first significant obstacle.  If it leaves less than the existing 1.0 gross R,
the trade is rejected.

No fitted distance, R cap, post-entry movement, partial exit or outcome
information is introduced.  Channel rotations retain channel objectives and
channel-edge reversals remain diagnostic-only.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from easychart_re1_channel_abstention import (
    ChannelAbstainingMicroEngine,
    EasyChartRE1ChannelAbstentionBundle,
)
from easychart_re1_efficient_objective import (
    PIVOT_ONLY_OBJECTIVE_BOOK_RULE,
    EfficientFirstMicroObstacleMixin,
    PivotOnlyObjectiveBook,
)
from easychart_re1_fine_objective import FIRST_MICRO_OBSTACLE_RULE
from easychart_re1_flow_ob_responsibility import ResponsibleFlowValidatedDecisionAreaEngine
from easychart_re1_flow_ob_sweep_responsibility import ResponsibleFlowMajorSwingEngine


SIGNIFICANT_MICRO_SWING_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIRST_SIGNIFICANT_MICRO_OBSTACLE_IS_THE_NEAREST_STILL_UNSPENT_CAUSALLY_CONFIRMED_SPAN6_ONE_MINUTE_OPPOSING_SWING_AVAILABLE_BEFORE_ENTRY"
)
if SIGNIFICANT_MICRO_SWING_OBJECTIVE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (SIGNIFICANT_MICRO_SWING_OBJECTIVE_RULE,)


class SignificantMicroObjectiveMixin(EfficientFirstMicroObstacleMixin):
    """Restrict the pivot-only objective book to the existing major-local span."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entry_micro_structure = PivotOnlyObjectiveBook(
            self.symbol,
            self.trigger_minutes,
            self.tick_size,
            pivot_spans=(6,),
        )

    @property
    def significant_micro_objective_diagnostics(self) -> dict[str, Any]:
        return {
            "objective": self.micro_objective_diagnostics,
            "pivot_spans": (6,),
            "rules": (
                FIRST_MICRO_OBSTACLE_RULE,
                PIVOT_ONLY_OBJECTIVE_BOOK_RULE,
                SIGNIFICANT_MICRO_SWING_OBJECTIVE_RULE,
            ),
        }


class SignificantObjectiveMicroEngine(
    SignificantMicroObjectiveMixin,
    ChannelAbstainingMicroEngine,
):
    pass


class SignificantObjectiveMajorSwingEngine(
    SignificantMicroObjectiveMixin,
    ResponsibleFlowMajorSwingEngine,
):
    pass


class SignificantObjectiveDecisionOBEngine(
    SignificantMicroObjectiveMixin,
    ResponsibleFlowValidatedDecisionAreaEngine,
):
    pass


class EasyChartRE1SignificantMicroObjectiveBundle(EasyChartRE1ChannelAbstentionBundle):
    """Quality reversal core targeting the first significant 1m/5m/15m obstacle."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = SignificantObjectiveMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = SignificantObjectiveMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = SignificantObjectiveDecisionOBEngine(
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
        output["first_significant_micro_obstacle"] = {
            "micro": self.micro.significant_micro_objective_diagnostics,
            "major_swing": self.major_swing.significant_micro_objective_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.significant_micro_objective_diagnostics,
            "rule_provenance": SIGNIFICANT_MICRO_SWING_OBJECTIVE_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1SignificantMicroObjectiveBundle
