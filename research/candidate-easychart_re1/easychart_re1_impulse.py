"""Pullback-resumption EasyChart policy with immutable first-obstacle objectives.

The response-confirmed candidate generalized a 60-minute BOS into an indefinitely
tradable direction.  The disclosed random intervals showed why this was wrong:
every loss was still labelled continuation even when the original impulse had
already become a pullback, range, or late trend.  A human chart trader does not
trade a direction label.  The complete episode is:

    higher-timeframe break -> counter-direction pullback -> local structure
    resumes the higher-timeframe side -> entry-location interaction -> immediate
    lower-timeframe response -> first real opposing obstacle.

This module encodes that sequence without a clock, volatility threshold, score,
fixed R target, or result-dependent parameter.  It also repairs two related
translation errors which created artificial 8R--15R plans:

* a selected nearby objective is immutable; plan construction cannot replace it
  with a later equal-width channel extension;
* every setup uses the first still-live opposing 5-minute/15-minute structure.
  If that first obstacle does not provide at least the account contract's 1R,
  the scenario has no clean day-trade geometry and is rejected rather than
  skipping to a remote target.

Execution footprints are tightened at the same semantic boundary.  The OB/FVG
price zone itself must overlap the projected structure, an executable order
block must satisfy the source's two-times body reliability test, and the first
post-retest candle must close beyond the retest extreme rather than merely stay
one tick outside the zone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_confirmed import (
    ConfirmedRepeatedDefenseScenarioEngine,
    PendingFootprintResponse,
)
from easychart_re1_phase import (
    EasyChartRE1PhaseBundle,
    PhaseConfirmedSelectiveScenarioEngine,
)
from easychart_zones import PriceZone, ZoneKind
from scenario_context_v5 import ScenarioContextMixin
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


PULLBACK_RESUMPTION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "SIXTY_MINUTE_BREAK_IS_EXECUTABLE_ONLY_AFTER_OPPOSING_FIFTEEN_MINUTE_PULLBACK_BREAK_AND_LATER_SAME_SIDE_RESUMPTION_BREAK"
)
FIRST_OBSTACLE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ONE_FULL_DAYTRADE_TARGET_IS_THE_FIRST_STILL_LIVE_OPPOSING_FIVE_OR_FIFTEEN_MINUTE_STRUCTURE"
)
IMMUTABLE_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "SELECTED_OBJECTIVE_SOURCE_CANNOT_BE_REPLACED_BY_A_FARTHER_CHANNEL_EXTENSION_AT_ENTRY"
)
FOOTPRINT_LOCATION_RULE = (
    "SOURCE_EXPLICIT:"
    "EXECUTION_OB_OR_FVG_PRICE_ZONE_OVERLAPS_THE_TRADED_STRUCTURE_LOCATION"
)
STRONG_OB_RULE = (
    "SOURCE_EXPLICIT:"
    "EXECUTABLE_ORDER_BLOCK_REQUIRES_AT_LEAST_TWO_TIMES_ENGULFING_BODY_OR_EQUIVALENT_OB_FVG_CONFLUENCE"
)
FOOTPRINT_RESPONSE_EXTENSION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIRST_POST_RETEST_MICRO_CLOSE_MUST_EXTEND_BEYOND_THE_RETEST_EXTREME"
)
for _rule in (
    PULLBACK_RESUMPTION_RULE,
    FIRST_OBSTACLE_RULE,
    IMMUTABLE_OBJECTIVE_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)
for _rule in (FOOTPRINT_LOCATION_RULE, STRONG_OB_RULE):
    if _rule not in _contracts.SOURCE_RULES:
        _contracts.SOURCE_RULES += (_rule,)
if FOOTPRINT_RESPONSE_EXTENSION_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (FOOTPRINT_RESPONSE_EXTENSION_RULE,)


TargetChoice = tuple[StructureZone, float, str | None, float | None]


class FirstObstacleObjectiveMixin:
    """Choose and preserve the first causal 5m/15m obstacle."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.decision_objectives = NearestAnyPivotStructureBook(
            self.symbol,
            self.decision_minutes,
            self.tick_size,
        )
        self._first_obstacle_counts: dict[str, int] = {}

    def _objective_inc(self, key: str) -> None:
        self._first_obstacle_counts[key] = self._first_obstacle_counts.get(key, 0) + 1

    @staticmethod
    def _nearest(side: Side, choices: list[tuple[str, TargetChoice]]) -> tuple[str, TargetChoice]:
        if side is Side.LONG:
            return min(enumerate(choices), key=lambda item: (item[1][1][1], item[0]))[1]
        return max(enumerate(choices), key=lambda item: (item[1][1][1], -item[0]))[1]

    def _append_unique(
        self,
        choices: list[tuple[str, TargetChoice]],
        source: str,
        value: TargetChoice | None,
    ) -> None:
        if value is None:
            return
        zone, price, _, _ = value
        for _, existing in choices:
            existing_zone, existing_price, _, _ = existing
            if (
                existing_zone.source_structure_id == zone.source_structure_id
                or abs(existing_price - price) <= self.tick_size * 0.5
            ):
                return
        choices.append((source, value))

    @staticmethod
    def _pivot_choice(value: tuple[StructureZone, float] | None) -> TargetChoice | None:
        if value is None:
            return None
        zone, price = value
        return zone, price, None, None

    def _select_target(
        self,
        context: StructureZone,
        side: Side,
        path: ScenarioPath,
        bar: Candle,
    ) -> TargetChoice | None:
        # Bypass ChannelExtensionTargetScenarioEngine here.  The base context
        # policy supplies the actual opposite channel edge for a rejection and
        # a pre-existing opposing structure for an accepted break.
        contextual = ScenarioContextMixin._select_target(self, context, side, path, bar)
        higher_pivot = self.structure.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        decision_pivot = self.decision_objectives.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        choices: list[tuple[str, TargetChoice]] = []
        self._append_unique(choices, "CONTEXTUAL_STRUCTURE", contextual)
        self._append_unique(choices, "15M_OPPOSING_PIVOT", self._pivot_choice(higher_pivot))
        self._append_unique(choices, "5M_OPPOSING_PIVOT", self._pivot_choice(decision_pivot))
        if not choices:
            self._objective_inc("no_first_obstacle")
            return None
        source, selected = self._nearest(side, choices)
        zone, price, _, _ = selected
        self._objective_inc(f"selected_{source.lower()}")
        self._trace(
            "first_obstacle_objective_selected",
            bar.ts_close_ns,
            side=side.name,
            path=path.value,
            selected_source=source,
            selected_zone_id=zone.zone_id,
            selected_price=price,
            candidates=[
                {
                    "source": candidate_source,
                    "zone_id": candidate[0].zone_id,
                    "price": candidate[1],
                    "timeframe_minutes": candidate[0].timeframe_minutes,
                }
                for candidate_source, candidate in choices
            ],
            rule_provenance=FIRST_OBSTACLE_RULE,
        )
        return selected

    def _channel_target_at(
        self,
        setup: ScenarioSetup,
        time_ns: int,
    ) -> tuple[StructureZone, float] | None:
        # Only project the exact channel edge already selected at interaction.
        # Never substitute an equal-width extension or a different objective.
        target = setup.target_zone
        if target is None or target.family is not StructureFamily.CHANNEL:
            return None
        source_id = target.source_structure_id
        edge = source_id.rsplit(":", 1)[-1]
        if edge not in {"LOWER", "UPPER"}:
            return None
        channel = self.structure.channel_for_boundary(source_id)
        if channel is None:
            return None
        zone = self.structure.channel_edge_snapshot(channel, edge, time_ns)
        price = channel.lower_at(time_ns) if edge == "LOWER" else channel.upper_at(time_ns)
        return zone, price

    def on_bar(self, timeframe_minutes: int, bar: Candle):  # type: ignore[no-untyped-def]
        if timeframe_minutes != self.decision_minutes:
            return super().on_bar(timeframe_minutes, bar)
        self.decision_objectives.on_bar(bar)
        plans = super().on_bar(timeframe_minutes, bar)
        # The current decision candle may select a pre-existing obstacle before
        # that candle consumes it for later unrelated episodes.
        self.decision_objectives.observe_price(bar)
        return plans

    @property
    def first_obstacle_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._first_obstacle_counts.items())),
            "decision_structure": dict(self.decision_objectives.diagnostics),
            "target_rule": FIRST_OBSTACLE_RULE,
            "immutability_rule": IMMUTABLE_OBJECTIVE_RULE,
        }


class StrongLocatedFootprintMixin:
    """Require the footprint itself, not merely its candle wick, at structure."""

    def _formation_touches_context(self, zone: PriceZone, setup: ScenarioSetup) -> bool:
        _, lower, upper = self._projected_bounds(setup, zone.observed_time_ns)
        overlaps = zone.lower <= upper and zone.upper >= lower
        if not overlaps:
            self._inc("footprint_zone_not_at_structure_deferred")
        return overlaps

    def _overlapping_context_order_blocks(
        self,
        fvg: PriceZone,
        setup: ScenarioSetup,
    ) -> list[PriceZone]:
        return [
            zone
            for zone in super()._overlapping_context_order_blocks(fvg, setup)
            if zone.high_quality_by_size
        ]

    def _select_footprint(
        self,
        candidates: list[PriceZone],
        setup: ScenarioSetup,
    ) -> PriceZone | None:
        qualified = [
            zone
            for zone in candidates
            if zone.kind is ZoneKind.FVG
            or (zone.kind is ZoneKind.ORDER_BLOCK and zone.high_quality_by_size)
        ]
        self._inc("weak_order_block_deferred",)
        return super()._select_footprint(qualified, setup)

    @staticmethod
    def _response_holds(
        setup: ScenarioSetup,
        pending: PendingFootprintResponse,
        bar: Candle,
    ) -> bool:
        return (
            bar.close > pending.retest_high
            if setup.side is Side.LONG
            else bar.close < pending.retest_low
        )


class ImpulseNaturalScenarioEngine(
    FirstObstacleObjectiveMixin,
    StrongLocatedFootprintMixin,
    PhaseConfirmedSelectiveScenarioEngine,
):
    """Ordered diagonal geometry with strong footprints and natural objectives."""


class ImpulseHorizontalScenarioEngine(
    FirstObstacleObjectiveMixin,
    StrongLocatedFootprintMixin,
    ConfirmedRepeatedDefenseScenarioEngine,
):
    """Repeated-defense sweep family with the same execution/target semantics."""


@dataclass(slots=True)
class ImpulseState:
    side: Side
    break_time_ns: int
    broken_pivot_id: str
    broken_price: float
    origin_pivot_id: str | None
    origin_price: float | None
    active: bool = True
    pullback_break_time_ns: int | None = None
    pullback_pivot_id: str | None = None
    resumption_break_time_ns: int | None = None
    resumption_pivot_id: str | None = None


class EasyChartRE1ImpulseBundle(EasyChartRE1PhaseBundle):
    """One plan stream requiring a complete HTF impulse/pullback/resumption."""

    LOCAL_DIRECTION_PIVOT_SPAN = 2

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = ImpulseNaturalScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal = ImpulseHorizontalScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0
        self._audit_offsets["horizontal"] = 0
        self._impulse: ImpulseState | None = None
        self._seen_macro_break_pivot_id: str | None = None
        self._broken_local_pivot_ids: set[str] = set()
        self._impulse_counts: dict[str, int] = {}

    def _impulse_inc(self, key: str) -> None:
        self._impulse_counts[key] = self._impulse_counts.get(key, 0) + 1

    def _latest_macro_origin(self, side: Side, before_time_ns: int):  # type: ignore[no-untyped-def]
        wanted = "LOW" if side is Side.LONG else "HIGH"
        return max(
            (
                pivot
                for pivot in self.macro_structure.pivots
                if pivot.side == wanted
                and pivot.event_time_ns < before_time_ns
                and pivot.observed_time_ns < before_time_ns
            ),
            key=lambda item: (item.event_time_ns, item.observed_time_ns, item.pivot_id),
            default=None,
        )

    def _refresh_macro_impulse(self, bar: Candle) -> None:
        pivot = self._last_direction_pivot
        side = self._macro_side
        if pivot is not None and side is not None and pivot.pivot_id != self._seen_macro_break_pivot_id:
            origin = self._latest_macro_origin(side, bar.ts_close_ns)
            self._seen_macro_break_pivot_id = pivot.pivot_id
            self._impulse = ImpulseState(
                side=side,
                break_time_ns=bar.ts_close_ns,
                broken_pivot_id=pivot.pivot_id,
                broken_price=pivot.price,
                origin_pivot_id=None if origin is None else origin.pivot_id,
                origin_price=None if origin is None else origin.price,
            )
            self._impulse_inc("macro_impulse_started")
            self._bundle_trace.append(
                {
                    "scenario_kind": "macro_impulse_started",
                    "event_time_ns": bar.ts_close_ns,
                    "symbol": self.symbol,
                    "side": side.name,
                    "broken_pivot_id": pivot.pivot_id,
                    "broken_price": pivot.price,
                    "origin_pivot_id": None if origin is None else origin.pivot_id,
                    "origin_price": None if origin is None else origin.price,
                    "rule_provenance": PULLBACK_RESUMPTION_RULE,
                },
            )
            return

        impulse = self._impulse
        if impulse is None or not impulse.active or impulse.origin_price is None:
            return
        invalidated = (
            bar.close <= impulse.origin_price
            if impulse.side is Side.LONG
            else bar.close >= impulse.origin_price
        )
        if invalidated:
            impulse.active = False
            self._impulse_inc("macro_impulse_origin_invalidated")
            self._bundle_trace.append(
                {
                    "scenario_kind": "macro_impulse_origin_invalidated",
                    "event_time_ns": bar.ts_close_ns,
                    "symbol": self.symbol,
                    "side": impulse.side.name,
                    "origin_price": impulse.origin_price,
                    "close": bar.close,
                    "rule_provenance": PULLBACK_RESUMPTION_RULE,
                },
            )

    def _new_local_breaks(self, bar: Candle):  # type: ignore[no-untyped-def]
        output = []
        for pivot in self.micro.structure.pivots:
            if (
                pivot.span != self.LOCAL_DIRECTION_PIVOT_SPAN
                or pivot.pivot_id in self._broken_local_pivot_ids
                or pivot.observed_time_ns >= bar.ts_close_ns
            ):
                continue
            side = None
            if pivot.side == "HIGH" and bar.close > pivot.price:
                side = Side.LONG
            elif pivot.side == "LOW" and bar.close < pivot.price:
                side = Side.SHORT
            if side is None:
                continue
            self._broken_local_pivot_ids.add(pivot.pivot_id)
            output.append((side, pivot))
        return output

    def _advance_local_phase(self, bar: Candle) -> None:
        impulse = self._impulse
        if impulse is None or not impulse.active:
            return
        breaks = self._new_local_breaks(bar)
        if not breaks:
            return
        side, pivot = max(
            breaks,
            key=lambda item: (
                item[1].event_time_ns,
                item[1].observed_time_ns,
                item[1].pivot_id,
            ),
        )
        if side is impulse.side:
            if (
                impulse.pullback_break_time_ns is not None
                and impulse.pullback_break_time_ns > impulse.break_time_ns
                and bar.ts_close_ns > impulse.pullback_break_time_ns
            ):
                impulse.resumption_break_time_ns = bar.ts_close_ns
                impulse.resumption_pivot_id = pivot.pivot_id
                self._impulse_inc("local_resumption_confirmed")
                kind = "local_resumption_confirmed"
            else:
                self._impulse_inc("same_side_break_before_pullback_deferred")
                kind = "same_side_break_before_pullback_deferred"
        else:
            impulse.pullback_break_time_ns = bar.ts_close_ns
            impulse.pullback_pivot_id = pivot.pivot_id
            impulse.resumption_break_time_ns = None
            impulse.resumption_pivot_id = None
            self._impulse_inc("local_pullback_confirmed")
            kind = "local_pullback_confirmed"
        self._bundle_trace.append(
            {
                "scenario_kind": kind,
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "macro_side": impulse.side.name,
                "local_break_side": side.name,
                "pivot_id": pivot.pivot_id,
                "pivot_price": pivot.price,
                "pullback_break_time_ns": impulse.pullback_break_time_ns,
                "resumption_break_time_ns": impulse.resumption_break_time_ns,
                "rule_provenance": PULLBACK_RESUMPTION_RULE,
            },
        )

    def _route_plan(self, plan: V5TradePlan) -> bool:
        if not super()._route_plan(plan):
            return False
        impulse = self._impulse
        valid = bool(
            impulse is not None
            and impulse.active
            and plan.side is impulse.side
            and impulse.pullback_break_time_ns is not None
            and impulse.resumption_break_time_ns is not None
            and impulse.break_time_ns < impulse.pullback_break_time_ns < impulse.resumption_break_time_ns
            and plan.interaction_time_ns >= impulse.pullback_break_time_ns
            and plan.observed_time_ns >= impulse.resumption_break_time_ns
        )
        if valid:
            self._impulse_inc("plan_allowed_complete_pullback_resumption")
            return True
        self._impulse_inc("plan_deferred_incomplete_pullback_resumption")
        self._bundle_trace.append(
            {
                "scenario_kind": "plan_deferred_incomplete_pullback_resumption",
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "side": plan.side.name,
                "interaction_time_ns": plan.interaction_time_ns,
                "macro_impulse": None if impulse is None else {
                    "side": impulse.side.name,
                    "active": impulse.active,
                    "break_time_ns": impulse.break_time_ns,
                    "pullback_break_time_ns": impulse.pullback_break_time_ns,
                    "resumption_break_time_ns": impulse.resumption_break_time_ns,
                },
                "rule_provenance": PULLBACK_RESUMPTION_RULE,
            },
        )
        return False

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        plans = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes == self.CONTEXT_MINUTES:
            self._refresh_macro_impulse(bar)
        elif timeframe_minutes == 15:
            self._advance_local_phase(bar)
        return plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        impulse = self._impulse
        output["pullback_resumption_router"] = {
            "counts": dict(sorted(self._impulse_counts.items())),
            "state": None if impulse is None else {
                "side": impulse.side.name,
                "break_time_ns": impulse.break_time_ns,
                "broken_pivot_id": impulse.broken_pivot_id,
                "origin_pivot_id": impulse.origin_pivot_id,
                "origin_price": impulse.origin_price,
                "active": impulse.active,
                "pullback_break_time_ns": impulse.pullback_break_time_ns,
                "pullback_pivot_id": impulse.pullback_pivot_id,
                "resumption_break_time_ns": impulse.resumption_break_time_ns,
                "resumption_pivot_id": impulse.resumption_pivot_id,
            },
            "rule_provenance": PULLBACK_RESUMPTION_RULE,
        }
        output["natural_objectives"] = {
            "micro": self.micro.first_obstacle_diagnostics,
            "horizontal": self.horizontal.first_obstacle_diagnostics,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ImpulseBundle
