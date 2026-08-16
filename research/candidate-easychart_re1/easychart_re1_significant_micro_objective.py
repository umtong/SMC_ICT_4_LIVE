"""First significant one-minute opposing swing as the day-trade objective.

Every tiny one-minute pivot is not a meaningful obstacle.  The source repeatedly
uses an important prior high/low or opposing structure, and the existing RE1
translation assigns span 6 to the larger local auction while span 2 is the
smallest reaction.  Only a causally confirmed span-6 opposing swing may refine
the inherited objective.

A completed channel acceptance or rejection is a price-transfer event, so its
first meaningful opposing swing is still an obstacle.  Only an actual channel
rotation retains the explicit opposite channel edge; the previous blanket
"any channel member" exception allowed accepted breaks to manufacture distant
5R+ objectives through already visible local structure.

No fitted distance, R cap, post-entry movement, partial exit or outcome
information is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup
from domain import Candle
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
CHANNEL_TRANSFER_FIRST_OBSTACLE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:CHANNEL_ACCEPTANCE_OR_REJECTION_TARGETS_THE_"
    "FIRST_SIGNIFICANT_OPPOSING_STRUCTURE_WHILE_ONLY_CHANNEL_ROTATION_RETAINS_"
    "THE_EXPLICIT_OPPOSITE_CHANNEL_EDGE"
)
for _rule in (
    SIGNIFICANT_MICRO_SWING_OBJECTIVE_RULE,
    CHANNEL_TRANSFER_FIRST_OBSTACLE_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class SignificantMicroObjectiveMixin(EfficientFirstMicroObstacleMixin):
    """Use span-6 micro structure for every non-rotation day-trade event."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entry_micro_structure = PivotOnlyObjectiveBook(
            self.symbol,
            self.trigger_minutes,
            self.tick_size,
            pivot_spans=(6,),
        )

    def _refine_target(self, setup: ScenarioSetup, bar: Candle) -> None:
        if setup.path is ScenarioPath.ROTATION:
            self._minc("channel_rotation_objective_retained")
            return
        if setup.target_price is None:
            return
        target = self.entry_micro_structure.target_for(
            setup.side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=setup.context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        if target is None:
            self._minc("no_significant_micro_obstacle_before_entry")
            return
        zone, price = target
        if not self._closer(setup.side, price, setup.target_price):
            self._minc("existing_objective_already_nearer")
            return
        previous_zone_id = None if setup.target_zone is None else setup.target_zone.zone_id
        previous_price = setup.target_price
        setup.target_zone = zone
        setup.target_price = price
        self._audit(zone)
        self._minc("objective_replaced_by_first_significant_micro_obstacle")
        self._trace(
            "objective_replaced_by_first_significant_micro_obstacle",
            bar.ts_close_ns,
            setup,
            previous_target_zone_id=previous_zone_id,
            previous_target_price=previous_price,
            selected_target_zone_id=zone.zone_id,
            selected_target_price=price,
            rule_provenance=(
                SIGNIFICANT_MICRO_SWING_OBJECTIVE_RULE,
                CHANNEL_TRANSFER_FIRST_OBSTACLE_RULE,
            ),
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
                CHANNEL_TRANSFER_FIRST_OBSTACLE_RULE,
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
            "rules": (
                SIGNIFICANT_MICRO_SWING_OBJECTIVE_RULE,
                CHANNEL_TRANSFER_FIRST_OBSTACLE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1SignificantMicroObjectiveBundle
