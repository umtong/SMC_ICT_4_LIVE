"""Coherent EasyChart RE1 policy from the supplied live-trade geometry.

The prior candidate already had the correct causal order:
pre-existing structure -> 5-minute auction event -> event-local footprint ->
first distinct 1-minute retest/response -> immutable full-position plan.

Two remaining translation errors produced trades a discretionary chart trader
would rarely accept:

* an accepted channel break used the far edge of the first equal-width
  extension even though the channel material names the extension midline as
  the first objective and the extension edge as the later objective;
* a repeated-defense horizontal fade remained executable against an already
  established 15-minute BOS direction, although that family represents a
  range/contraction trap rather than a fade of active directional acceptance.

This module changes only those responsibilities.  It does not add a score,
volatility threshold, clock timeout, fixed-R target, daily rule, trade limit,
partial exit, stop ratchet, or outcome-dependent choice.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

import contracts_v5 as _contracts
from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import Pivot, ScenarioPath, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_confirmed import ConfirmedSelectiveScenarioEngine
from easychart_re1_major_swing import EasyChartRE1MajorSwingBundle
from easychart_zones import ZoneSide


class CoherentObjectiveKind(str, Enum):
    FIRST_EXTENSION_MIDLINE = "FIRST_EXTENSION_MIDLINE"


FIRST_EXTENSION_MIDLINE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ONE_FULL_POSITION_CHANNEL_BREAK_TARGETS_FIRST_EXTENSION_MIDLINE_BEFORE_EXTENSION_EDGE"
)
HORIZONTAL_LOCAL_DIRECTION_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "REPEATED_DEFENSE_RANGE_FADE_IS_NOT_EXECUTABLE_AGAINST_ACTIVE_FIFTEEN_MINUTE_BOS"
)
if FIRST_EXTENSION_MIDLINE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (FIRST_EXTENSION_MIDLINE_RULE,)
if HORIZONTAL_LOCAL_DIRECTION_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (HORIZONTAL_LOCAL_DIRECTION_RULE,)


class FirstExtensionMidlineScenarioEngine(ConfirmedSelectiveScenarioEngine):
    """Response-confirmed diagonal engine whose first extension target is 50%."""

    def _channel_extension_at(
        self,
        channel: Any,
        side: Side,
        time_ns: int,
    ) -> tuple[StructureZone, float]:
        lower_edge = channel.lower_at(time_ns)
        upper_edge = channel.upper_at(time_ns)
        width = upper_edge - lower_edge
        if width <= self.tick_size:
            raise RuntimeError("channel width is not positive")

        if side is Side.LONG:
            price = upper_edge + 0.5 * width
            lower = price
            upper = price + self.tick_size
            zone_side = ZoneSide.RESISTANCE
            invalidation = upper + self.tick_size
        else:
            price = lower_edge - 0.5 * width
            lower = price - self.tick_size
            upper = price
            zone_side = ZoneSide.SUPPORT
            invalidation = lower - self.tick_size

        source_id = f"{channel.channel_id}:FIRST_EXTENSION_MIDLINE:{side.name}"
        zone = StructureZone(
            zone_id=f"{source_id}:SNAP:{time_ns}",
            kind=CoherentObjectiveKind.FIRST_EXTENSION_MIDLINE,
            family=StructureFamily.CHANNEL,
            side=zone_side,
            timeframe_minutes=channel.timeframe_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=price,
            formed_index=0,
            formed_time_ns=channel.second_time_ns,
            observed_time_ns=channel.observed_time_ns,
            formation_indices=(),
            strength_ratio=channel.strength_ratio,
            source_structure_id=source_id,
            source_pivot_span=channel.pivot_span,
        )
        return zone, price


class EasyChartRE1CoherentBundle(EasyChartRE1MajorSwingBundle):
    """Integrated account stream with natural channel and range-state geometry."""

    LOCAL_CONTEXT_MINUTES = 15
    LOCAL_DIRECTION_PIVOT_SPAN = 2

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = FirstExtensionMidlineScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

        self.local_direction_structure = LifecycleAwareStructureBook(
            symbol,
            self.LOCAL_CONTEXT_MINUTES,
            tick_size,
            pivot_spans=(self.LOCAL_DIRECTION_PIVOT_SPAN,),
        )
        self._local_side: Side | None = None
        self._last_local_direction_pivot: Pivot | None = None
        self._broken_local_direction_pivot_ids: set[str] = set()
        self._local_router_counts: dict[str, int] = {}

    def _local_inc(self, key: str) -> None:
        self._local_router_counts[key] = self._local_router_counts.get(key, 0) + 1

    def _newly_broken_local_pivots(self, bar: Candle) -> list[tuple[Side, Pivot]]:
        output: list[tuple[Side, Pivot]] = []
        for pivot in self.local_direction_structure.pivots:
            if pivot.span != self.LOCAL_DIRECTION_PIVOT_SPAN:
                continue
            if pivot.pivot_id in self._broken_local_direction_pivot_ids:
                continue
            if pivot.observed_time_ns >= bar.ts_close_ns:
                continue
            side: Side | None = None
            if pivot.side == "HIGH" and bar.close > pivot.price:
                side = Side.LONG
            elif pivot.side == "LOW" and bar.close < pivot.price:
                side = Side.SHORT
            if side is None:
                continue
            self._broken_local_direction_pivot_ids.add(pivot.pivot_id)
            output.append((side, pivot))
        return output

    def _advance_local_direction(self, bar: Candle) -> None:
        breaks = self._newly_broken_local_pivots(bar)
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
        changed = side is not self._local_side
        self._local_side = side
        self._last_local_direction_pivot = pivot
        self._local_inc("local_bos_events")
        if changed:
            self._local_inc("local_direction_changes")
        self._bundle_trace.append(
            {
                "scenario_kind": "local_fifteen_minute_direction_break",
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "side": side.name,
                "pivot_id": pivot.pivot_id,
                "pivot_side": pivot.side,
                "pivot_price": pivot.price,
                "pivot_event_time_ns": pivot.event_time_ns,
                "pivot_observed_time_ns": pivot.observed_time_ns,
                "pivot_span": pivot.span,
                "close": bar.close,
                "direction_changed": changed,
                "rule_provenance": HORIZONTAL_LOCAL_DIRECTION_RULE,
            },
        )

    def _update_local_direction(self, bar: Candle) -> None:
        self.local_direction_structure.on_bar(bar)
        self._advance_local_direction(bar)
        self.local_direction_structure.observe_price(bar)

    def _route_plan(self, plan: V5TradePlan) -> bool:
        if not super()._route_plan(plan):
            return False

        against_local_direction = (
            plan.scale_name == "HORIZONTAL"
            and plan.scenario_path == ScenarioPath.REJECTION.value
            and self._local_side is not None
            and plan.side is not self._local_side
        )
        if against_local_direction:
            self._local_inc("horizontal_rejected_against_local_direction")
            self._bundle_trace.append(
                {
                    "scenario_kind": "horizontal_rejected_against_local_direction",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "setup_id": plan.setup_id,
                    "side": plan.side.name,
                    "local_side": self._local_side.name,
                    "local_break_pivot_id": (
                        None
                        if self._last_local_direction_pivot is None
                        else self._last_local_direction_pivot.pivot_id
                    ),
                    "scenario_path": plan.scenario_path,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": HORIZONTAL_LOCAL_DIRECTION_RULE,
                },
            )
            return False

        self._local_inc("local_direction_route_allowed")
        return True

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == self.LOCAL_CONTEXT_MINUTES:
            self._update_local_direction(bar)
        return super().on_bar(timeframe_minutes, bar)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["coherent_channel_objective_policy"] = {
            "name": "FIRST_EXTENSION_MIDLINE_OR_NEARER_PREEXISTING_STRUCTURE",
            "rule_provenance": FIRST_EXTENSION_MIDLINE_RULE,
        }
        output["local_direction_router"] = {
            "policy": (
                "15M close-confirmed span-2 BOS blocks repeated-defense range fades "
                "against active local initiative"
            ),
            "current_side": "NEUTRAL" if self._local_side is None else self._local_side.name,
            "last_direction_pivot_id": (
                None
                if self._last_local_direction_pivot is None
                else self._last_local_direction_pivot.pivot_id
            ),
            "counts": dict(sorted(self._local_router_counts.items())),
            "structure": dict(self.local_direction_structure.diagnostics),
            "rule_provenance": HORIZONTAL_LOCAL_DIRECTION_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1CoherentBundle
