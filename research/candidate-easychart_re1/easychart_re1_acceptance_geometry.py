"""Natural entry geometry for completed local acceptance transfers.

A break-and-hold changes the role of a boundary, but the day-trade entry occurs
at its lower-frame return.  Two pieces of geometry therefore belong to the
accepted transfer itself:

* invalidation is beyond both the retested projected boundary and that return's
  lower-frame extreme, not the distant origin of the old breakout impulse;
* the first opposing micro swing created *after* the accepted break is a real
  obstacle.  A causally confirmed span-2 or span-6 one-minute pivot formed
  during the transfer replaces a farther channel extension or coarse target.

This produces the skilled-trader connection naturally: first valid return,
short structural stop, nearby pre-existing objective and therefore mostly
ordinary 1--2R plans.  If the first transfer-local obstacle leaves less than the
existing 1R minimum, there is no trade.  No fitted R cap, ATR rule, score,
session rule, partial exit or outcome information is used.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import Pivot, ScenarioPath, ScenarioSetup, V5TradePlan
from domain import Candle, Side
from easychart_re1_embedded_acceptance_response import (
    EasyChartRE1EmbeddedAcceptanceResponseBundle,
    ResponseEmbeddedNaturalHorizontalEngine,
    ResponseEmbeddedResponsibleFlowMajorSwingEngine,
    ResponseEmbeddedResponsibleFlowValidatedDecisionAreaEngine,
    ResponseEmbeddedResponsiblePhaseFlowMicroEngine,
)
from easychart_re1_efficient_objective import PivotOnlyObjectiveBook


ACCEPTANCE_RETEST_INVALIDATION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:AN_ACCEPTED_TRANSFER_RETURN_IS_INVALID_BEYOND_"
    "BOTH_THE_RETESTED_PROJECTED_BOUNDARY_AND_THE_RETURN_EXTREME"
)
ACCEPTANCE_LOCAL_SWING_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_FIRST_UNSPENT_CAUSALLY_CONFIRMED_SPAN2_OR_"
    "SPAN6_ONE_MINUTE_OPPOSING_SWING_FORMED_AFTER_THE_ACCEPTED_BREAK_IS_THE_"
    "TRANSFER_OBJECTIVE_BEFORE_A_MORE_DISTANT_CHANNEL_OR_COARSE_STRUCTURE"
)
for _rule in (
    ACCEPTANCE_RETEST_INVALIDATION_RULE,
    ACCEPTANCE_LOCAL_SWING_OBJECTIVE_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class AcceptedTransferGeometryMixin:
    """Refine only acceptance plans; rejection responsibility is untouched."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.acceptance_micro_structure = PivotOnlyObjectiveBook(
            self.symbol,
            self.trigger_minutes,
            self.tick_size,
            pivot_spans=(2, 6),
        )
        self._accepted_geometry_counts: dict[str, int] = {}

    def _aginc(self, key: str) -> None:
        self._accepted_geometry_counts[key] = (
            self._accepted_geometry_counts.get(key, 0) + 1
        )

    def _acceptance_stop(
        self,
        setup: ScenarioSetup,
        time_ns: int,
    ) -> float | None:
        bar = getattr(self, "_current_trigger_bar", None)
        if bar is None or bar.ts_close_ns != time_ns:
            return super()._acceptance_stop(setup, time_ns)
        _, lower, upper = self._projected_bounds(setup, time_ns)
        stop = (
            min(float(bar.low), lower) - self.tick_size
            if setup.side is Side.LONG
            else max(float(bar.high), upper) + self.tick_size
        )
        self._aginc("accepted_retest_extreme_invalidation_selected")
        self._trace(
            "accepted_retest_extreme_invalidation_selected",
            time_ns,
            setup,
            projected_lower=lower,
            projected_upper=upper,
            retest_low=bar.low,
            retest_high=bar.high,
            stop=stop,
            rule_provenance=ACCEPTANCE_RETEST_INVALIDATION_RULE,
        )
        return stop

    def _transfer_pivots(
        self,
        setup: ScenarioSetup,
        bar: Candle,
    ) -> list[Pivot]:
        wanted = "HIGH" if setup.side is Side.LONG else "LOW"
        return [
            pivot
            for pivot in self.acceptance_micro_structure.pivots
            if pivot.side == wanted
            and pivot.event_time_ns >= setup.interaction_time_ns
            and pivot.observed_time_ns < bar.ts_close_ns
            and not (
                pivot.consumed_time_ns is not None
                and pivot.consumed_time_ns < bar.ts_close_ns
            )
            and (
                pivot.price > bar.high
                if setup.side is Side.LONG
                else pivot.price < bar.low
            )
        ]

    def _refine_accepted_transfer_objective(
        self,
        setup: ScenarioSetup,
        bar: Candle,
    ) -> None:
        if setup.path is not ScenarioPath.ACCEPTANCE or setup.target_price is None:
            return
        candidates = self._transfer_pivots(setup, bar)
        if not candidates:
            self._aginc("no_transfer_local_micro_obstacle")
            return
        selected = (
            min(
                candidates,
                key=lambda pivot: (
                    pivot.price,
                    -pivot.span,
                    pivot.observed_time_ns,
                    pivot.pivot_id,
                ),
            )
            if setup.side is Side.LONG
            else max(
                candidates,
                key=lambda pivot: (
                    pivot.price,
                    pivot.span,
                    -pivot.observed_time_ns,
                    pivot.pivot_id,
                ),
            )
        )
        closer = (
            selected.price < setup.target_price
            if setup.side is Side.LONG
            else selected.price > setup.target_price
        )
        if not closer:
            self._aginc("existing_objective_before_transfer_local_swing")
            return
        previous_zone = None if setup.target_zone is None else setup.target_zone.zone_id
        previous_price = setup.target_price
        zone = self.acceptance_micro_structure._horizontal_snapshot(
            selected,
            bar.ts_close_ns,
        )
        setup.target_zone = zone
        setup.target_price = selected.price
        self._audit(zone)
        self._aginc(f"objective_replaced_by_transfer_span{selected.span}_swing")
        self._trace(
            "accepted_transfer_local_swing_objective_selected",
            bar.ts_close_ns,
            setup,
            previous_target_zone_id=previous_zone,
            previous_target_price=previous_price,
            selected_target_zone_id=zone.zone_id,
            selected_target_price=selected.price,
            pivot_id=selected.pivot_id,
            pivot_event_time_ns=selected.event_time_ns,
            pivot_observed_time_ns=selected.observed_time_ns,
            pivot_span=selected.span,
            rule_provenance=ACCEPTANCE_LOCAL_SWING_OBJECTIVE_RULE,
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
        self._refine_accepted_transfer_objective(setup, bar)
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
        self.acceptance_micro_structure.on_bar(bar)
        plans = super().on_bar(timeframe_minutes, bar)
        self.acceptance_micro_structure.observe_price(bar)
        return plans

    @property
    def accepted_transfer_geometry_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._accepted_geometry_counts.items())),
            "micro_structure": dict(self.acceptance_micro_structure.diagnostics),
            "pivot_spans": (2, 6),
            "rules": (
                ACCEPTANCE_RETEST_INVALIDATION_RULE,
                ACCEPTANCE_LOCAL_SWING_OBJECTIVE_RULE,
            ),
        }


class GeometryEmbeddedMicroEngine(
    AcceptedTransferGeometryMixin,
    ResponseEmbeddedResponsiblePhaseFlowMicroEngine,
):
    pass


class GeometryEmbeddedHorizontalEngine(
    AcceptedTransferGeometryMixin,
    ResponseEmbeddedNaturalHorizontalEngine,
):
    pass


class GeometryEmbeddedMajorSwingEngine(
    AcceptedTransferGeometryMixin,
    ResponseEmbeddedResponsibleFlowMajorSwingEngine,
):
    pass


class GeometryEmbeddedDecisionOBEngine(
    AcceptedTransferGeometryMixin,
    ResponseEmbeddedResponsibleFlowValidatedDecisionAreaEngine,
):
    pass


class EasyChartRE1AcceptanceGeometryBundle(
    EasyChartRE1EmbeddedAcceptanceResponseBundle
):
    """Response-confirmed acceptance with transfer-local stop and objective."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = GeometryEmbeddedMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = GeometryEmbeddedHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = GeometryEmbeddedMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = GeometryEmbeddedDecisionOBEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "horizontal", "major_swing", "flow_decision_ob"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["accepted_transfer_geometry"] = {
            "micro": self.micro.accepted_transfer_geometry_diagnostics,
            "horizontal": self.horizontal.accepted_transfer_geometry_diagnostics,
            "major_swing": self.major_swing.accepted_transfer_geometry_diagnostics,
            "flow_decision_ob": (
                self.flow_decision_ob.accepted_transfer_geometry_diagnostics
            ),
            "rules": (
                ACCEPTANCE_RETEST_INVALIDATION_RULE,
                ACCEPTANCE_LOCAL_SWING_OBJECTIVE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1AcceptanceGeometryBundle
