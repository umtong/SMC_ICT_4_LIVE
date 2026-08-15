"""Common-factor displacement -> rebalance -> continuation for EasyChart RE1.

The source's FVG/OB material describes a complete continuation auction rather
than an unconditional box touch: aggressive expansion creates a footprint,
price returns to rebalance it, then the original direction resumes.  The
reversal core does not own this state.  This module adds one independent family
whose evidence is available before entry:

1. the four-symbol strategy has an active BTC+ETH plus three-of-four common
   initiative state;
2. a causally confirmed 15-minute span-2 BOS agrees with that common direction;
3. a high-quality five-minute engulfing OB is born in that direction and all
   completed one-minute constituents show aligned cumulative taker flow and
   net price progress;
4. the first later one-minute touch fixes the structural stop at the OB
   formation wick; the first following completed minute must close beyond the
   touch candle's favorable extreme while the common factor still holds;
5. the nearest still-unspent confirmed 5m/15m opposite pivot is the immutable
   objective and at least 1.0 gross R must remain before submission.

The common-factor requirement distinguishes broad information processing from a
local liquidity sweep.  No session, ATR, percentile, clock expiry, score,
fixed-R target, partial exit or stop movement is introduced.  A footprint
expires because its first interaction, invalidation, objective consumption, or
common-factor state ends -- never because a fitted number of bars elapsed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import Pivot, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_channel_abstention import EasyChartRE1ChannelAbstentionBundle
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from easychart_zones import EasyChartZoneDetector, PriceZone, ZoneKind, ZoneSide
from execution_re1_market_factor import (
    COMMON_FACTOR_MIDPOINT_HYSTERESIS_RULE,
    CROSS_ASSET_COMMON_INITIATIVE_RULE,
    CommonFactorState,
    EasyChartRE1MarketFactorStrategy,
)
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


FACTOR_CONTINUATION_FORMATION_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "ACTIVE_COMMON_CRYPTO_INITIATIVE_PLUS_ALIGNED_FIFTEEN_MINUTE_BOS_AND_FLOW_VALIDATED_FIVE_MINUTE_OB_DEFINE_A_CONTINUATION_DECISION_AREA"
)
FACTOR_CONTINUATION_FIRST_RETURN_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FLOW_VALIDATED_FIVE_MINUTE_OB_CONTINUATION_ENTERS_ONLY_AFTER_FIRST_LATER_TOUCH_AND_FIRST_COMPLETED_ONE_MINUTE_RESPONSE"
)
for _rule in (FACTOR_CONTINUATION_FORMATION_RULE, FACTOR_CONTINUATION_FIRST_RETURN_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


@dataclass(slots=True)
class FactorContinuationSetup:
    setup_id: str
    side: Side
    source_zone: PriceZone
    factor_event_time_ns: int
    factor_sequence: int
    local_pivot_id: str
    target_zone: StructureZone
    target_price: float
    first_touch_time_ns: int | None = None
    touch_high: float | None = None
    touch_low: float | None = None
    terminal_reason: str | None = None

    @property
    def active(self) -> bool:
        return self.terminal_reason is None


class FactorContinuationEngine:
    """Small causal state machine for five-minute common-shock pullbacks."""

    DIRECTION_SPAN = 2

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        *,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.minimum_gross_rr = minimum_gross_rr
        self.higher_minutes = 15
        self.decision_minutes = 5
        self.trigger_minutes = 1

        self.local_structure = NearestAnyPivotStructureBook(
            symbol,
            self.higher_minutes,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.decision_structure = NearestAnyPivotStructureBook(
            symbol,
            self.decision_minutes,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.footprints = EasyChartZoneDetector(symbol, self.decision_minutes, tick_size)
        self.flow_analyzer = CausalFlowAnalyzer(tick_size)

        self.factor_state: CommonFactorState | None = None
        self.local_side: Side | None = None
        self.last_direction_pivot: Pivot | None = None
        self._broken_direction_pivots: set[str] = set()
        self._pending_formations: dict[str, PriceZone] = {}
        self._active: dict[str, FactorContinuationSetup] = {}
        self.setups: list[FactorContinuationSetup] = []
        self.plans: list[V5TradePlan] = []
        self.audit_zones: list[Any] = []
        self._zone_lookup: dict[str, Any] = {}
        self._trace_records: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _trace(self, kind: str, time_ns: int, **values: Any) -> None:
        self._trace_records.append(
            {
                "scenario_kind": kind,
                "event_time_ns": time_ns,
                "symbol": self.symbol,
                **values,
            }
        )

    def _audit(self, zone: Any) -> None:
        zone_id = getattr(zone, "zone_id", None)
        if zone_id and zone_id not in self._zone_lookup:
            self._zone_lookup[zone_id] = zone
            self.audit_zones.append(zone)

    @staticmethod
    def _zone_side(zone: PriceZone) -> Side:
        return Side.LONG if zone.side is ZoneSide.SUPPORT else Side.SHORT

    @staticmethod
    def _aligned(side: Side, value: float) -> bool:
        return value > 0.0 if side is Side.LONG else value < 0.0

    @staticmethod
    def _progress(side: Side, start: float, end: float) -> float:
        return end - start if side is Side.LONG else start - end

    def set_market_factor_state(self, state: CommonFactorState | None) -> None:
        self.factor_state = state

    def _new_direction_breaks(self, bar: Candle) -> list[tuple[Side, Pivot]]:
        output: list[tuple[Side, Pivot]] = []
        for pivot in self.local_structure.pivots:
            if pivot.span != self.DIRECTION_SPAN:
                continue
            if pivot.pivot_id in self._broken_direction_pivots:
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
            self._broken_direction_pivots.add(pivot.pivot_id)
            output.append((side, pivot))
        return output

    def _on_fifteen(self, bar: Candle) -> None:
        self.local_structure.on_bar(bar)
        breaks = self._new_direction_breaks(bar)
        if breaks:
            side, pivot = max(
                breaks,
                key=lambda item: (
                    item[1].event_time_ns,
                    item[1].observed_time_ns,
                    item[1].pivot_id,
                ),
            )
            changed = side is not self.local_side
            self.local_side = side
            self.last_direction_pivot = pivot
            self._inc("local_direction_break")
            self._trace(
                "factor_continuation_local_direction_break",
                bar.ts_close_ns,
                side=side.name,
                pivot_id=pivot.pivot_id,
                pivot_price=pivot.price,
                direction_changed=changed,
                rule_provenance=FACTOR_CONTINUATION_FORMATION_RULE,
            )
        self.local_structure.observe_price(bar)

    def _on_five(self, bar: Candle) -> None:
        self.decision_structure.on_bar(bar)
        created = self.footprints.on_bar(bar)
        for zone in created:
            self._audit(zone)
            if zone.kind is not ZoneKind.ORDER_BLOCK or not zone.high_quality_by_size:
                continue
            side = self._zone_side(zone)
            state = self.factor_state
            if state is None or state.side is not side:
                self._inc("five_minute_ob_without_aligned_common_factor")
                continue
            if self.local_side is not side or self.last_direction_pivot is None:
                self._inc("five_minute_ob_without_aligned_local_bos")
                continue
            self._pending_formations[zone.zone_id] = zone
            self._inc("five_minute_ob_waiting_complete_constituent_flow")
            self._trace(
                "factor_continuation_ob_waiting_complete_flow",
                bar.ts_close_ns,
                zone_id=zone.zone_id,
                side=side.name,
                factor_event_time_ns=state.event_time_ns,
                factor_sequence=state.sequence,
                local_direction_pivot_id=self.last_direction_pivot.pivot_id,
                rule_provenance=FACTOR_CONTINUATION_FORMATION_RULE,
            )
        self.decision_structure.observe_price(bar)

    def _formation_flow(self, zone: PriceZone, side: Side) -> tuple[list[FlowObservation], float, float] | None:
        observations = [
            item
            for item in self.flow_analyzer.history
            if zone.formed_time_ns < item.ts_close_ns <= zone.observed_time_ns
        ]
        if not observations:
            return None
        cumulative = sum(item.signed_taker_quote for item in observations)
        progress = self._progress(side, observations[0].open, observations[-1].close)
        aligned = [
            item
            for item in observations
            if item.active
            and item.directed
            and item.material_progress
            and self._aligned(side, item.signed_taker_quote)
            and (item.body > 0.0 if side is Side.LONG else item.body < 0.0)
        ]
        if not self._aligned(side, cumulative) or progress <= 0.0 or not aligned:
            return None
        return observations, cumulative, progress

    def _nearest_target(
        self,
        side: Side,
        *,
        time_ns: int,
        high: float,
        low: float,
    ) -> tuple[StructureZone, float] | None:
        candidates: list[tuple[str, StructureZone, float]] = []
        for name, book in (("5M", self.decision_structure), ("15M", self.local_structure)):
            target = book.target_for(
                side,
                interaction_time_ns=time_ns,
                source_span=2,
                current_high=high,
                current_low=low,
            )
            if target is not None:
                candidates.append((name, target[0], target[1]))
        if not candidates:
            return None
        selected = (
            min(candidates, key=lambda item: (item[2], item[0]))
            if side is Side.LONG
            else max(candidates, key=lambda item: (item[2], item[0]))
        )
        self._audit(selected[1])
        return selected[1], selected[2]

    def _finalize_pending_formations(self, bar: Candle) -> None:
        for zone_id, zone in list(self._pending_formations.items()):
            if zone.observed_time_ns > bar.ts_close_ns:
                continue
            self._pending_formations.pop(zone_id, None)
            if zone.observed_time_ns != bar.ts_close_ns:
                self._inc("pending_ob_missed_same_close_constituent")
                continue
            side = self._zone_side(zone)
            state = self.factor_state
            if (
                state is None
                or state.side is not side
                or self.local_side is not side
                or self.last_direction_pivot is None
            ):
                self._inc("pending_ob_context_changed_before_flow_completion")
                continue
            evidence = self._formation_flow(zone, side)
            if evidence is None:
                self._inc("five_minute_ob_rejected_without_aligned_flow")
                continue
            observations, cumulative, progress = evidence
            target = self._nearest_target(
                side,
                time_ns=bar.ts_close_ns,
                high=zone.impulse_extreme if side is Side.LONG else bar.high,
                low=bar.low if side is Side.LONG else zone.impulse_extreme,
            )
            if target is None:
                self._inc("flow_validated_ob_without_preexisting_target")
                continue
            target_zone, target_price = target
            setup_id = f"FACTOR_CONTINUATION:{zone.zone_id}:{state.event_time_ns}"
            if any(item.setup_id == setup_id for item in self.setups):
                continue
            setup = FactorContinuationSetup(
                setup_id=setup_id,
                side=side,
                source_zone=zone,
                factor_event_time_ns=state.event_time_ns,
                factor_sequence=state.sequence,
                local_pivot_id=self.last_direction_pivot.pivot_id,
                target_zone=target_zone,
                target_price=target_price,
            )
            self.setups.append(setup)
            self._active[setup_id] = setup
            self._inc("factor_continuation_setup_armed")
            self._trace(
                "factor_continuation_setup_armed",
                bar.ts_close_ns,
                setup_id=setup_id,
                side=side.name,
                source_zone_id=zone.zone_id,
                source_lower=zone.lower,
                source_upper=zone.upper,
                source_invalidation=zone.invalidation,
                target_zone_id=target_zone.zone_id,
                target_price=target_price,
                factor_event_time_ns=state.event_time_ns,
                factor_sequence=state.sequence,
                local_direction_pivot_id=self.last_direction_pivot.pivot_id,
                formation_bars=len(observations),
                cumulative_signed_taker_quote=cumulative,
                net_price_progress=progress,
                rule_provenance=(
                    CROSS_ASSET_COMMON_INITIATIVE_RULE,
                    FACTOR_CONTINUATION_FORMATION_RULE,
                ),
            )

    def _finish(self, setup: FactorContinuationSetup, reason: str, time_ns: int, **values: Any) -> None:
        setup.terminal_reason = reason
        self._active.pop(setup.setup_id, None)
        self._inc(reason)
        self._trace(
            reason,
            time_ns,
            setup_id=setup.setup_id,
            side=setup.side.name,
            source_zone_id=setup.source_zone.zone_id,
            **values,
        )

    @staticmethod
    def _target_touched(setup: FactorContinuationSetup, bar: Candle) -> bool:
        return bar.high >= setup.target_price if setup.side is Side.LONG else bar.low <= setup.target_price

    @staticmethod
    def _stop_touched(setup: FactorContinuationSetup, bar: Candle) -> bool:
        stop = setup.source_zone.invalidation
        return bar.low <= stop if setup.side is Side.LONG else bar.high >= stop

    def _refresh_target(self, setup: FactorContinuationSetup, bar: Candle) -> None:
        target = self._nearest_target(
            setup.side,
            time_ns=bar.ts_close_ns,
            high=bar.high,
            low=bar.low,
        )
        if target is None:
            return
        zone, price = target
        closer = price < setup.target_price if setup.side is Side.LONG else price > setup.target_price
        if closer:
            setup.target_zone = zone
            setup.target_price = price
            self._inc("factor_continuation_target_refreshed_to_nearer_structure")

    def _make_plan(self, setup: FactorContinuationSetup, bar: Candle) -> V5TradePlan | None:
        self._refresh_target(setup, bar)
        entry = bar.close
        stop = setup.source_zone.invalidation
        target = setup.target_price
        risk = entry - stop if setup.side is Side.LONG else stop - entry
        reward = target - entry if setup.side is Side.LONG else entry - target
        if risk <= 0.0 or reward <= 0.0:
            self._inc("factor_continuation_nonpositive_geometry")
            return None
        gross_rr = reward / risk
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._inc("factor_continuation_below_minimum_gross_rr")
            return None
        plan_id = f"PLAN:{setup.setup_id}:{bar.ts_close_ns}"
        plan = V5TradePlan(
            plan_id=plan_id,
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family="FACTOR_CONTINUATION_5M_OB_FIRST_RETURN",
            side=setup.side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=setup.source_zone.zone_id,
            higher_zone_kind=setup.source_zone.kind,
            higher_strength_ratio=setup.source_zone.strength_ratio,
            lower_zone_id=setup.source_zone.zone_id,
            lower_zone_kind=setup.source_zone.kind,
            lower_strength_ratio=setup.source_zone.strength_ratio,
            trigger_zone_id=setup.source_zone.zone_id,
            trigger_strength_ratio=setup.source_zone.strength_ratio,
            target_zone_id=setup.target_zone.zone_id,
            target_zone_kind=setup.target_zone.kind,
            overlap_lower=setup.source_zone.lower,
            overlap_upper=setup.source_zone.upper,
            interaction_time_ns=setup.first_touch_time_ns or setup.source_zone.observed_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path="ACCEPTANCE",
            setup_observed_time_ns=setup.source_zone.observed_time_ns,
            trigger_zone_kind="FACTOR_CONTINUATION_FIRST_RESPONSE",
            source_rule_count=2,
            rule_provenance=(
                CROSS_ASSET_COMMON_INITIATIVE_RULE,
                COMMON_FACTOR_MIDPOINT_HYSTERESIS_RULE,
                FACTOR_CONTINUATION_FORMATION_RULE,
                FACTOR_CONTINUATION_FIRST_RETURN_RULE,
            ),
            scale_name="FACTOR_CONTINUATION",
            higher_timeframe_minutes=15,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        self.plans.append(plan)
        self._inc("factor_continuation_plan_created")
        self._trace(
            "factor_continuation_plan_created",
            bar.ts_close_ns,
            setup_id=setup.setup_id,
            plan_id=plan_id,
            side=setup.side.name,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            factor_event_time_ns=setup.factor_event_time_ns,
            rule_provenance=plan.rule_provenance,
        )
        return plan

    def _advance_setups(self, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for setup in list(self._active.values()):
            state = self.factor_state
            if state is None or state.side is not setup.side:
                self._finish(
                    setup,
                    "factor_continuation_common_state_ended_before_entry",
                    bar.ts_close_ns,
                    factor_event_time_ns=setup.factor_event_time_ns,
                )
                continue
            if self.local_side is not setup.side:
                self._finish(
                    setup,
                    "factor_continuation_local_direction_changed_before_entry",
                    bar.ts_close_ns,
                )
                continue
            if bar.ts_close_ns <= setup.source_zone.observed_time_ns:
                continue
            if self._target_touched(setup, bar):
                self._finish(setup, "factor_continuation_target_spent_before_entry", bar.ts_close_ns)
                continue
            if self._stop_touched(setup, bar):
                self._finish(setup, "factor_continuation_source_invalidated_before_entry", bar.ts_close_ns)
                continue

            if setup.first_touch_time_ns is None:
                touched = bar.low <= setup.source_zone.upper and bar.high >= setup.source_zone.lower
                if not touched:
                    continue
                setup.first_touch_time_ns = bar.ts_close_ns
                setup.touch_high = bar.high
                setup.touch_low = bar.low
                self._inc("factor_continuation_first_touch_waiting_response")
                self._trace(
                    "factor_continuation_first_touch_waiting_response",
                    bar.ts_close_ns,
                    setup_id=setup.setup_id,
                    side=setup.side.name,
                    source_zone_id=setup.source_zone.zone_id,
                    touch_high=bar.high,
                    touch_low=bar.low,
                    rule_provenance=FACTOR_CONTINUATION_FIRST_RETURN_RULE,
                )
                continue

            if bar.ts_close_ns <= setup.first_touch_time_ns:
                continue
            assert setup.touch_high is not None and setup.touch_low is not None
            confirms = (
                bar.close > setup.touch_high
                if setup.side is Side.LONG
                else bar.close < setup.touch_low
            )
            if not confirms:
                self._finish(
                    setup,
                    "factor_continuation_first_response_failed",
                    bar.ts_close_ns,
                    touch_high=setup.touch_high,
                    touch_low=setup.touch_low,
                    response_close=bar.close,
                )
                continue
            plan = self._make_plan(setup, bar)
            if plan is None:
                self._finish(
                    setup,
                    "factor_continuation_no_trade_geometry",
                    bar.ts_close_ns,
                )
                continue
            self._finish(
                setup,
                "factor_continuation_planned",
                bar.ts_close_ns,
                plan_id=plan.plan_id,
            )
            output.append(plan)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 15:
            self._on_fifteen(bar)
            return []
        if timeframe_minutes == 5:
            self._on_five(bar)
            return []
        if timeframe_minutes != 1:
            return []
        self.flow_analyzer.observe(bar)
        self._finalize_pending_formations(bar)
        return self._advance_setups(bar)

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self._trace_records
        self._trace_records = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        if zone_id in self._zone_lookup:
            return self._zone_lookup[zone_id]
        for book in (self.local_structure, self.decision_structure):
            pivot = book.pivot_for_structure(zone_id)
            if pivot is not None:
                return book._horizontal_snapshot(pivot, pivot.observed_time_ns)
        return None

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._counts.items())),
            "active_setups": len(self._active),
            "pending_formations": len(self._pending_formations),
            "local_side": None if self.local_side is None else self.local_side.name,
            "last_direction_pivot_id": None
            if self.last_direction_pivot is None
            else self.last_direction_pivot.pivot_id,
            "local_structure": dict(self.local_structure.diagnostics),
            "decision_structure": dict(self.decision_structure.diagnostics),
            "footprints": dict(self.footprints.diagnostics),
            "flow": self.flow_analyzer.diagnostics,
            "rules": (
                FACTOR_CONTINUATION_FORMATION_RULE,
                FACTOR_CONTINUATION_FIRST_RETURN_RULE,
            ),
        }


class FactorContinuationMarketStrategy(EasyChartRE1MarketFactorStrategy):
    """Publish each causal common-factor state to factor-aware symbol engines."""

    def _observe_common_factor(self) -> None:
        super()._observe_common_factor()
        for engine in self.scenario_engines.values():
            setter = getattr(engine, "set_market_factor_state", None)
            if setter is not None:
                setter(self.factor_state)


class EasyChartRE1FactorContinuationBundle(EasyChartRE1ChannelAbstentionBundle):
    """Quality reversal core plus one independent common-factor continuation family."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.factor_continuation = FactorContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["factor_continuation"] = 0
        self._factor_continuation_trace: list[dict[str, Any]] = []
        self._factor_continuation_counts: dict[str, int] = {}

    def _finc(self, key: str) -> None:
        self._factor_continuation_counts[key] = self._factor_continuation_counts.get(key, 0) + 1

    def set_market_factor_state(self, state: CommonFactorState | None) -> None:
        self.factor_continuation.set_market_factor_state(state)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.factor_continuation.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.factor_continuation.plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        existing = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes not in {15, 5, 1}:
            return existing
        raw = self.factor_continuation.on_bar(timeframe_minutes, bar)
        self._sync_audit("factor_continuation", self.factor_continuation)
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._finc("factor_continuation_overlapped_existing_episode")
                continue
            if not self._route_plan(plan):
                self._finc("factor_continuation_rejected_by_macro_context")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._finc("factor_continuation_plan_allowed")
            self._factor_continuation_trace.append(
                {
                    "scenario_kind": "factor_continuation_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": plan.rule_provenance,
                }
            )
        return existing + output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            super().drain_trace()
            + self.factor_continuation.drain_trace()
            + self._factor_continuation_trace
        )
        self._factor_continuation_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.factor_continuation.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["factor_continuation_family"] = {
            "routing_counts": dict(sorted(self._factor_continuation_counts.items())),
            "engine": self.factor_continuation.diagnostics,
            "rules": (
                FACTOR_CONTINUATION_FORMATION_RULE,
                FACTOR_CONTINUATION_FIRST_RETURN_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1FactorContinuationBundle
