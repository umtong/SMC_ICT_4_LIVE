"""Sweep/reclaim core with the first unspent opposing one-minute footprint target.

The source's high-probability reversal is not a generic touch or bounce.  Price
first takes liquidity, then closes back through the decision boundary; the
sweep extreme owns invalidation and the first opposing structure owns the first
profit decision.  In the no-partial RE1 contract that first decision is the full
exit.

Earlier candidates represented only 5m/15m pivots as objectives, producing
remote 3R-10R plans even when a visible one-minute OB/FVG lay directly ahead.
This module adds a separate causal one-minute objective detector:

* only OB/FVG zones observed before the entry close are eligible;
* the footprint must satisfy the source's existing >=2x displacement criterion;
* it must remain uninvalidated and untouched;
* a long targets the near edge of the first resistance footprint and a short the
  near edge of the first support footprint;
* if that first obstacle leaves less than the existing 1.0 gross R, the trade is
  rejected rather than skipping it for a farther target.

Only sweep/reclaim REJECTION paths reach the account.  Simple first-touch
bounces and accepted-break trades remain available in other regime-specific
families, but do not share this reversal responsibility.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, V5TradePlan
from domain import Candle, Side
from easychart_re1_direct_sweep_ob import (
    DirectSweepOBDecisionAreaEngine,
    EasyChartRE1DirectSweepOBBundle,
)
from easychart_re1_flow_ob_sweep_responsibility import (
    ResponsibleFlowMajorSwingEngine,
    ResponsibleLiquiditySweepFlowDecisionAreaEngine,
    ResponsiblePhaseFlowMicroEngine,
)
from easychart_zones import EasyChartZoneDetector, PriceZone, ZoneSide


FIRST_UNSPENT_MICRO_FOOTPRINT_TARGET_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FULL_POSITION_SWEEP_RECLAIM_TARGETS_THE_NEAR_EDGE_OF_THE_FIRST_PREEXISTING_UNTOUCHED_HIGH_QUALITY_ONE_MINUTE_OPPOSING_OB_OR_FVG"
)
REJECTION_ONLY_RESPONSIBILITY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "LIQUIDITY_REVERSAL_CORE_EXECUTES_ONLY_A_BOUNDARY_SWEEP_AND_FULL_RECLAIM_NOT_A_SIMPLE_BOUNCE_OR_ACCEPTED_BREAK"
)
for _rule in (
    FIRST_UNSPENT_MICRO_FOOTPRINT_TARGET_RULE,
    REJECTION_ONLY_RESPONSIBILITY_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class FirstOpposingMicroFootprintObjectiveMixin:
    """Replace a coarser objective only with a genuinely pre-existing footprint."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.micro_objective_detector = EasyChartZoneDetector(
            self.symbol,
            self.trigger_minutes,
            self.tick_size,
        )
        self._micro_footprint_target_counts: dict[str, int] = {}

    def _mft_inc(self, key: str) -> None:
        self._micro_footprint_target_counts[key] = (
            self._micro_footprint_target_counts.get(key, 0) + 1
        )

    @staticmethod
    def _closer(side: Side, candidate: float, existing: float) -> bool:
        return candidate < existing if side is Side.LONG else candidate > existing

    def _eligible_micro_targets(
        self,
        setup: ScenarioSetup,
        bar: Candle,
    ) -> list[tuple[PriceZone, float]]:
        wanted = ZoneSide.RESISTANCE if setup.side is Side.LONG else ZoneSide.SUPPORT
        output: list[tuple[PriceZone, float]] = []
        for zone in self.micro_objective_detector.zones:
            if (
                zone.side is not wanted
                or not zone.high_quality_by_size
                or not zone.active
                or zone.first_touch_time_ns is not None
                or zone.observed_time_ns >= bar.ts_close_ns
            ):
                continue
            if setup.side is Side.LONG:
                if zone.lower <= bar.high:
                    continue
                price = zone.lower
            else:
                if zone.upper >= bar.low:
                    continue
                price = zone.upper
            output.append((zone, price))
        return output

    def _refine_micro_footprint_target(
        self,
        setup: ScenarioSetup,
        bar: Candle,
    ) -> None:
        if setup.target_price is None:
            return
        candidates = self._eligible_micro_targets(setup, bar)
        if not candidates:
            self._mft_inc("no_preexisting_unspent_micro_footprint")
            return
        selected = (
            min(candidates, key=lambda item: (item[1], item[0].observed_time_ns, item[0].zone_id))
            if setup.side is Side.LONG
            else max(candidates, key=lambda item: (item[1], -item[0].observed_time_ns, item[0].zone_id))
        )
        zone, price = selected
        if not self._closer(setup.side, price, setup.target_price):
            self._mft_inc("coarser_objective_already_nearer")
            return
        previous_id = None if setup.target_zone is None else setup.target_zone.zone_id
        previous_price = setup.target_price
        setup.target_zone = zone  # PriceZone supplies the immutable target ID and kind.
        setup.target_price = price
        self._audit(zone)
        self._mft_inc("objective_replaced_by_micro_footprint")
        self._trace(
            "objective_replaced_by_micro_footprint",
            bar.ts_close_ns,
            setup,
            previous_target_zone_id=previous_id,
            previous_target_price=previous_price,
            target_zone_id=zone.zone_id,
            target_zone_kind=zone.kind.value,
            target_price=price,
            target_observed_time_ns=zone.observed_time_ns,
            target_strength_ratio=zone.strength_ratio,
            rule_provenance=FIRST_UNSPENT_MICRO_FOOTPRINT_TARGET_RULE,
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
        self._refine_micro_footprint_target(setup, bar)
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
        # Lifecycle is updated through the current completed bar before entry;
        # any target touched intrabar is therefore unavailable.
        self.micro_objective_detector.on_bar(bar)
        return super().on_bar(timeframe_minutes, bar)

    @property
    def micro_footprint_target_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._micro_footprint_target_counts.items())),
            "detector": dict(self.micro_objective_detector.diagnostics),
            "rule_provenance": FIRST_UNSPENT_MICRO_FOOTPRINT_TARGET_RULE,
        }


class RejectionTargetMicroEngine(
    FirstOpposingMicroFootprintObjectiveMixin,
    ResponsiblePhaseFlowMicroEngine,
):
    pass


class RejectionTargetMajorSwingEngine(
    FirstOpposingMicroFootprintObjectiveMixin,
    ResponsibleFlowMajorSwingEngine,
):
    pass


class RejectionTargetDecisionOBEngine(
    FirstOpposingMicroFootprintObjectiveMixin,
    ResponsibleLiquiditySweepFlowDecisionAreaEngine,
):
    pass


class RejectionTargetDirectSweepEngine(
    FirstOpposingMicroFootprintObjectiveMixin,
    DirectSweepOBDecisionAreaEngine,
):
    pass


class EasyChartRE1RejectionMicroTargetBundle(EasyChartRE1DirectSweepOBBundle):
    """One sweep/reclaim family set with a no-partial first-footprint objective."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = RejectionTargetMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = RejectionTargetMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = RejectionTargetDecisionOBEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.direct_sweep_ob = RejectionTargetDirectSweepEngine(
            symbol,
            tick_size,
            scale_name="DIRECT_SWEEP_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "major_swing", "flow_decision_ob", "direct_sweep_ob"):
            self._audit_offsets[key] = 0
        self._rejection_target_counts: dict[str, int] = {}

    def _rt_inc(self, key: str) -> None:
        self._rejection_target_counts[key] = self._rejection_target_counts.get(key, 0) + 1

    def _route_plan(self, plan: V5TradePlan) -> bool:
        if plan.scenario_path != ScenarioPath.REJECTION.value:
            self._rt_inc("non_rejection_plan_suppressed")
            return False
        allowed = super()._route_plan(plan)
        self._rt_inc("rejection_plan_allowed" if allowed else "rejection_plan_rejected_by_context")
        return allowed

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["rejection_micro_target_policy"] = {
            "counts": dict(sorted(self._rejection_target_counts.items())),
            "micro": self.micro.micro_footprint_target_diagnostics,
            "major_swing": self.major_swing.micro_footprint_target_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.micro_footprint_target_diagnostics,
            "direct_sweep_ob": self.direct_sweep_ob.micro_footprint_target_diagnostics,
            "rules": (
                FIRST_UNSPENT_MICRO_FOOTPRINT_TARGET_RULE,
                REJECTION_ONLY_RESPONSIBILITY_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1RejectionMicroTargetBundle
