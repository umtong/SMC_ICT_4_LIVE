"""First causal one-minute obstacle as the non-channel day-trade objective.

A large fraction of losing reversal plans reached one or more R before reversing,
while the machine waited for a distant 5/15-minute pivot.  The supplied material
uses the *first* opposing structure or the recent wave high/low, not the farthest
confirmed higher-frame object.  A skilled chart trader naturally sees a small
but already confirmed reaction swing which the coarse structure book omits.

This module fixes that representation rather than imposing an R cap:

* one-minute bars build a causal pivot book with the same delayed confirmation
  and lifecycle semantics already used elsewhere;
* immediately before entry, a still-unspent opposing one-minute pivot may
  replace the older objective only when it is closer in price;
* channel rotations keep their explicit channel objective because ordinary
  internal micro swings are not the channel thesis;
* if the first real obstacle offers less than 1.0 gross R, the inherited
  pre-entry geometry rule rejects the trade.

There is no fitted R threshold beyond the user's existing 1.0 minimum, no
outcome information and no post-entry management.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, StructureFamily, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow_ob import FlowValidatedOrderBlockDecisionStructureBook
from easychart_re1_flow_ob_responsibility import (
    EasyChartRE1ResponsibleFlowOBBundle,
    ResponsibleFlowValidatedDecisionAreaEngine,
)
from easychart_re1_flow_ob_sweep_responsibility import (
    ResponsibleFlowMajorSwingEngine,
    ResponsiblePhaseFlowMicroEngine,
)
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


FIRST_MICRO_OBSTACLE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "NON_CHANNEL_DAYTRADE_TARGET_IS_THE_FIRST_STILL_UNSPENT_CONFIRMED_ONE_FIVE_OR_FIFTEEN_MINUTE_OPPOSING_STRUCTURE_AVAILABLE_BEFORE_ENTRY"
)
if FIRST_MICRO_OBSTACLE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (FIRST_MICRO_OBSTACLE_RULE,)


class FirstMicroObstacleMixin:
    """Refine a fixed objective at entry using a causal 1m reaction swing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entry_micro_structure = NearestAnyPivotStructureBook(
            self.symbol,
            self.trigger_minutes,
            self.tick_size,
            pivot_spans=(2, 6),
        )
        self._micro_objective_counts: dict[str, int] = {}

    def _minc(self, key: str) -> None:
        self._micro_objective_counts[key] = self._micro_objective_counts.get(key, 0) + 1

    @staticmethod
    def _closer(side: Side, candidate: float, existing: float) -> bool:
        return candidate < existing if side is Side.LONG else candidate > existing

    def _refine_target(self, setup: ScenarioSetup, bar: Candle) -> None:
        if any(member.family is StructureFamily.CHANNEL for member in setup.context_members):
            self._minc("channel_objective_retained")
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
            self._minc("no_micro_obstacle_before_entry")
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
        self._minc("objective_replaced_by_first_micro_obstacle")
        self._trace(
            "objective_replaced_by_first_micro_obstacle",
            bar.ts_close_ns,
            setup,
            previous_target_zone_id=previous_zone_id,
            previous_target_price=previous_price,
            selected_target_zone_id=zone.zone_id,
            selected_target_price=price,
            rule_provenance=FIRST_MICRO_OBSTACLE_RULE,
        )

    def _make_plan(
        self,
        setup: ScenarioSetup,
        bar: Candle,
        *,
        entry: float,
        stop: float,
        trigger_zone: Any,
        trigger_kind: Any,
        trigger_strength: float,
    ) -> V5TradePlan | None:
        self._refine_target(setup, bar)
        return super()._make_plan(
            setup,
            bar,
            entry=entry,
            stop=stop,
            trigger_zone=trigger_zone,
            trigger_kind=trigger_kind,
            trigger_strength=trigger_strength,
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes != self.trigger_minutes:
            return super().on_bar(timeframe_minutes, bar)
        self.entry_micro_structure.on_bar(bar)
        plans = super().on_bar(timeframe_minutes, bar)
        self.entry_micro_structure.observe_price(bar)
        return plans

    @property
    def micro_objective_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._micro_objective_counts.items())),
            "structure": dict(self.entry_micro_structure.diagnostics),
            "rule_provenance": FIRST_MICRO_OBSTACLE_RULE,
        }


class FineObjectivePhaseFlowMicroEngine(
    FirstMicroObstacleMixin,
    ResponsiblePhaseFlowMicroEngine,
):
    pass


class FineObjectiveFlowMajorSwingEngine(
    FirstMicroObstacleMixin,
    ResponsibleFlowMajorSwingEngine,
):
    pass


class FineObjectiveFlowDecisionAreaEngine(
    FirstMicroObstacleMixin,
    ResponsibleFlowValidatedDecisionAreaEngine,
):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = FlowValidatedOrderBlockDecisionStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
            self.flow_analyzer,
        )


class EasyChartRE1FineObjectiveBundle(EasyChartRE1ResponsibleFlowOBBundle):
    """Original profitable families with the first causal micro obstacle target."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = FineObjectivePhaseFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = FineObjectiveFlowMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = FineObjectiveFlowDecisionAreaEngine(
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
        output["first_micro_obstacle_policy"] = {
            "micro": self.micro.micro_objective_diagnostics,
            "major_swing": self.major_swing.micro_objective_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.micro_objective_diagnostics,
            "rule_provenance": FIRST_MICRO_OBSTACLE_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1FineObjectiveBundle
