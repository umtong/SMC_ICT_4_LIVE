"""Nested local initiative, anchored fair-value pullback and first response.

This continuation family solves a different auction from the reversal cores.  A
causally confirmed five-minute swing break must agree with the current
fifteen-minute structure side and create a real five-minute engulfing footprint.
The footprint is accepted only when its completed constituent one-minute taker
flow and price progress agree with the break.

The impulse anchors a volume-weighted fair value.  The first later pullback to
either the still-valid source order block or that anchored fair value must close
back on the intended side; the first following completed minute must then close
beyond the pullback extreme.  Entry is that response close.  The full stop is
beyond both the pullback and footprint invalidation, and the immutable target is
the first pre-existing 5m/15m obstacle or the impulse wave extreme.

Only the latest nested impulse owns the symbol's continuation episode.  No ATR
threshold, fitted percentile, session rule, score, trade cap, partial exit or
post-entry stop movement is introduced.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import Pivot, ScenarioPath, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from easychart_zones import EasyChartZoneDetector, PriceZone, ZoneKind, ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


LOCAL_NESTED_INITIATIVE_RULE = (
    "RESEARCH_HYPOTHESIS:A_CAUSAL_FIVE_MINUTE_SWING_BREAK_ALIGNED_WITH_ACTIVE_"
    "FIFTEEN_MINUTE_STRUCTURE_AND_FLOW_VALIDATED_ENGULFING_OB_DEFINES_A_LOCAL_"
    "CONTINUATION_IMPULSE"
)
ANCHORED_FAIR_VALUE_PULLBACK_RULE = (
    "EXTERNAL_METHOD:THE_IMPULSE_START_ANCHORS_CAUSAL_VOLUME_WEIGHTED_FAIR_VALUE_"
    "AND_THE_FIRST_LATER_PULLBACK_TO_FAIR_VALUE_OR_SOURCE_OB_OWNS_THE_ENTRY_EPISODE"
)
LOCAL_CONTINUATION_RESPONSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_FIRST_COMPLETED_MINUTE_AFTER_THE_PULLBACK_"
    "MUST_CLOSE_BEYOND_ITS_FAVORABLE_EXTREME_WHILE_REMAINING_ON_THE_INTENDED_"
    "SIDE_OF_ANCHORED_FAIR_VALUE"
)
LOCAL_CONTINUATION_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_CONTINUATION_TARGET_IS_THE_NEAREST_"
    "PREEXISTING_UNSPENT_5M_15M_OPPOSING_SWING_OR_THE_IMPULSE_WAVE_EXTREME"
)
for _rule in (
    LOCAL_NESTED_INITIATIVE_RULE,
    ANCHORED_FAIR_VALUE_PULLBACK_RULE,
    LOCAL_CONTINUATION_RESPONSE_RULE,
    LOCAL_CONTINUATION_OBJECTIVE_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class LocalContinuationKind(str, Enum):
    IMPULSE_WAVE_HIGH = "IMPULSE_WAVE_HIGH"
    IMPULSE_WAVE_LOW = "IMPULSE_WAVE_LOW"
    ANCHORED_VWAP_PULLBACK = "ANCHORED_VWAP_PULLBACK"
    SOURCE_ORDER_BLOCK_PULLBACK = "SOURCE_ORDER_BLOCK_PULLBACK"


@dataclass(frozen=True, slots=True)
class MinuteWeight:
    time_ns: int
    typical_price: float
    weight: float
    open: float
    high: float
    low: float
    close: float


@dataclass(slots=True)
class PendingNestedImpulse:
    side: Side
    source_zone: PriceZone
    decision_bar: Candle
    broken_pivot_id: str
    local_pivot_id: str


@dataclass(slots=True)
class LocalContinuationSetup:
    setup_id: str
    side: Side
    source_zone: PriceZone
    impulse_time_ns: int
    impulse_start_ns: int
    impulse_high: float
    impulse_low: float
    broken_pivot_id: str
    local_pivot_id: str
    target_zone: StructureZone
    target_price: float
    vwap_numerator: float
    vwap_denominator: float
    state: str = "WAITING_PULLBACK"
    retest_time_ns: int | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    retest_kind: LocalContinuationKind | None = None
    terminal_reason: str | None = None

    @property
    def anchored_vwap(self) -> float:
        if self.vwap_denominator <= 0.0:
            raise RuntimeError("anchored VWAP has no causal weight")
        return self.vwap_numerator / self.vwap_denominator


class LocalAuctionContinuationEngine:
    """Latest nested local impulse -> first pullback -> first response."""

    DIRECTION_SPAN = 2
    NS_PER_MINUTE = 60_000_000_000

    def __init__(
        self,
        symbol: str,
        tick_size: float,
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
            15,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.decision_structure = NearestAnyPivotStructureBook(
            symbol,
            5,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.footprints = EasyChartZoneDetector(symbol, 5, tick_size)
        self.flow = CausalFlowAnalyzer(tick_size)
        self._minutes: deque[MinuteWeight] = deque(maxlen=2880)

        self.local_side: Side | None = None
        self.last_local_pivot: Pivot | None = None
        self._broken_local_ids: set[str] = set()
        self._broken_decision_ids: set[str] = set()
        self._pending: dict[int, list[PendingNestedImpulse]] = {}
        self._active: LocalContinuationSetup | None = None
        self._sequence = 0

        self.setups: list[LocalContinuationSetup] = []
        self.plans: list[V5TradePlan] = []
        self.audit_zones: list[Any] = []
        self._zones: dict[str, Any] = {}
        self._trace: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _record(self, kind: str, time_ns: int, **values: Any) -> None:
        self._trace.append(
            {
                "scenario_kind": kind,
                "event_time_ns": time_ns,
                "symbol": self.symbol,
                **values,
            },
        )

    def _audit(self, zone: Any) -> None:
        zone_id = getattr(zone, "zone_id", None)
        if zone_id and zone_id not in self._zones:
            self._zones[zone_id] = zone
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

    def _new_breaks(
        self,
        structure: NearestAnyPivotStructureBook,
        broken: set[str],
        bar: Candle,
    ) -> list[tuple[Side, Pivot]]:
        output: list[tuple[Side, Pivot]] = []
        for pivot in structure.pivots:
            if pivot.span != self.DIRECTION_SPAN:
                continue
            if pivot.pivot_id in broken or pivot.observed_time_ns >= bar.ts_close_ns:
                continue
            side: Side | None = None
            if pivot.side == "HIGH" and bar.close > pivot.price:
                side = Side.LONG
            elif pivot.side == "LOW" and bar.close < pivot.price:
                side = Side.SHORT
            if side is None:
                continue
            broken.add(pivot.pivot_id)
            output.append((side, pivot))
        return output

    def _on_fifteen(self, bar: Candle) -> None:
        self.local_structure.on_bar(bar)
        breaks = self._new_breaks(
            self.local_structure,
            self._broken_local_ids,
            bar,
        )
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
            self.last_local_pivot = pivot
            self._inc("local_direction_break")
            self._record(
                "local_continuation_fifteen_minute_direction_break",
                bar.ts_close_ns,
                side=side.name,
                pivot_id=pivot.pivot_id,
                pivot_price=pivot.price,
                direction_changed=changed,
                rule_provenance=LOCAL_NESTED_INITIATIVE_RULE,
            )
            if self._active is not None and self._active.side is not side:
                self._finish(
                    self._active,
                    "local_continuation_opposite_fifteen_minute_control",
                    bar.ts_close_ns,
                )
        self.local_structure.observe_price(bar)

    def _select_zone(
        self,
        created: list[PriceZone],
        side: Side,
        time_ns: int,
    ) -> PriceZone | None:
        wanted = ZoneSide.SUPPORT if side is Side.LONG else ZoneSide.RESISTANCE
        order_blocks = [
            zone
            for zone in created
            if zone.kind is ZoneKind.ORDER_BLOCK
            and zone.side is wanted
            and zone.high_quality_by_size
            and zone.observed_time_ns == time_ns
        ]
        if not order_blocks:
            return None
        return max(
            order_blocks,
            key=lambda zone: (
                zone.strength_ratio,
                zone.impulse_extreme,
                zone.zone_id,
            ),
        )

    def _on_five(self, bar: Candle) -> None:
        self.decision_structure.on_bar(bar)
        created = self.footprints.on_bar(bar)
        for zone in created:
            self._audit(zone)
        breaks = self._new_breaks(
            self.decision_structure,
            self._broken_decision_ids,
            bar,
        )
        if breaks:
            side, pivot = max(
                breaks,
                key=lambda item: (
                    item[1].event_time_ns,
                    item[1].observed_time_ns,
                    item[1].pivot_id,
                ),
            )
            if self._active is not None and self._active.side is not side:
                self._finish(
                    self._active,
                    "local_continuation_opposite_five_minute_break",
                    bar.ts_close_ns,
                )
            if (
                self.local_side is side
                and self.last_local_pivot is not None
            ):
                zone = self._select_zone(created, side, bar.ts_close_ns)
                if zone is None:
                    self._inc("nested_break_without_high_quality_order_block")
                else:
                    pending = PendingNestedImpulse(
                        side=side,
                        source_zone=zone,
                        decision_bar=bar,
                        broken_pivot_id=pivot.pivot_id,
                        local_pivot_id=self.last_local_pivot.pivot_id,
                    )
                    self._pending.setdefault(bar.ts_close_ns, []).append(pending)
                    self._inc("nested_impulse_waiting_complete_constituent_flow")
                    self._record(
                        "nested_impulse_waiting_complete_constituent_flow",
                        bar.ts_close_ns,
                        side=side.name,
                        zone_id=zone.zone_id,
                        broken_pivot_id=pivot.pivot_id,
                        local_pivot_id=self.last_local_pivot.pivot_id,
                        rule_provenance=LOCAL_NESTED_INITIATIVE_RULE,
                    )
        self.decision_structure.observe_price(bar)

    def _minute_weight(self, bar: Any) -> MinuteWeight:
        volume = float(getattr(bar, "volume", 0.0))
        quote = float(getattr(bar, "quote_volume", 0.0))
        close = float(bar.close)
        weight = volume if volume > 0.0 else quote / max(close, self.tick_size)
        typical = (float(bar.high) + float(bar.low) + close) / 3.0
        return MinuteWeight(
            time_ns=int(bar.ts_close_ns),
            typical_price=typical,
            weight=max(weight, 0.0),
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=close,
        )

    def _formation_minutes(self, pending: PendingNestedImpulse) -> list[MinuteWeight]:
        start = pending.decision_bar.ts_close_ns - 5 * self.NS_PER_MINUTE
        return [
            item
            for item in self._minutes
            if start < item.time_ns <= pending.decision_bar.ts_close_ns
        ]

    def _formation_flow(
        self,
        pending: PendingNestedImpulse,
    ) -> tuple[list[FlowObservation], float, float] | None:
        start = pending.decision_bar.ts_close_ns - 5 * self.NS_PER_MINUTE
        observations = [
            item
            for item in self.flow.history
            if start < item.ts_close_ns <= pending.decision_bar.ts_close_ns
        ]
        if not observations:
            return None
        cumulative = sum(item.signed_taker_quote for item in observations)
        progress = self._progress(
            pending.side,
            observations[0].open,
            observations[-1].close,
        )
        aligned = [
            item
            for item in observations
            if item.active
            and item.directed
            and item.material_progress
            and self._aligned(pending.side, item.signed_taker_quote)
            and (
                item.body > 0.0
                if pending.side is Side.LONG
                else item.body < 0.0
            )
        ]
        if not self._aligned(pending.side, cumulative) or progress <= 0.0 or not aligned:
            return None
        return observations, cumulative, progress

    def _wave_zone(
        self,
        pending: PendingNestedImpulse,
    ) -> tuple[StructureZone, float]:
        side = pending.side
        price = (
            pending.decision_bar.high
            if side is Side.LONG
            else pending.decision_bar.low
        )
        kind = (
            LocalContinuationKind.IMPULSE_WAVE_HIGH
            if side is Side.LONG
            else LocalContinuationKind.IMPULSE_WAVE_LOW
        )
        zone_side = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        source = f"LOCAL_CONTINUATION_WAVE:{pending.source_zone.zone_id}:{kind.value}"
        zone = StructureZone(
            zone_id=f"{source}:SNAP:{pending.decision_bar.ts_close_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=zone_side,
            timeframe_minutes=5,
            lower=price - self.tick_size * 0.5,
            upper=price + self.tick_size * 0.5,
            invalidation=(
                price + self.tick_size
                if side is Side.LONG
                else price - self.tick_size
            ),
            impulse_extreme=price,
            formed_index=0,
            formed_time_ns=pending.decision_bar.ts_close_ns,
            observed_time_ns=pending.decision_bar.ts_close_ns,
            formation_indices=(),
            strength_ratio=pending.source_zone.strength_ratio,
            source_structure_id=source,
            source_pivot_span=1,
        )
        self._audit(zone)
        return zone, price

    def _structure_targets(
        self,
        side: Side,
        bar: Candle,
    ) -> list[tuple[str, StructureZone, float]]:
        output: list[tuple[str, StructureZone, float]] = []
        for source, book in (
            ("5M", self.decision_structure),
            ("15M", self.local_structure),
        ):
            target = book.target_for(
                side,
                interaction_time_ns=bar.ts_close_ns,
                source_span=2,
                current_high=bar.high,
                current_low=bar.low,
            )
            if target is not None:
                output.append((source, target[0], target[1]))
        return output

    @staticmethod
    def _nearest(
        side: Side,
        choices: list[tuple[str, StructureZone, float]],
    ) -> tuple[str, StructureZone, float]:
        return (
            min(choices, key=lambda item: (item[2], item[0], item[1].zone_id))
            if side is Side.LONG
            else max(choices, key=lambda item: (item[2], item[0], item[1].zone_id))
        )

    def _arm_pending(self, time_ns: int) -> None:
        pending_items = self._pending.pop(time_ns, [])
        for pending in pending_items:
            evidence = self._formation_flow(pending)
            minutes = self._formation_minutes(pending)
            if evidence is None or len(minutes) < 5:
                self._inc("nested_impulse_rejected_without_complete_aligned_flow")
                continue
            observations, cumulative, progress = evidence
            numerator = sum(item.typical_price * item.weight for item in minutes)
            denominator = sum(item.weight for item in minutes)
            if denominator <= 0.0:
                self._inc("nested_impulse_without_vwap_weight")
                continue
            wave_zone, wave_price = self._wave_zone(pending)
            choices = [("IMPULSE_WAVE", wave_zone, wave_price)]
            choices.extend(
                self._structure_targets(pending.side, pending.decision_bar),
            )
            valid_choices = [
                item
                for item in choices
                if (
                    item[2] > pending.decision_bar.close
                    if pending.side is Side.LONG
                    else item[2] < pending.decision_bar.close
                )
            ]
            if not valid_choices:
                self._inc("nested_impulse_without_future_objective")
                continue
            _, target_zone, target_price = self._nearest(
                pending.side,
                valid_choices,
            )
            if self._active is not None:
                self._finish(
                    self._active,
                    "local_continuation_superseded_by_new_nested_impulse",
                    time_ns,
                )
            self._sequence += 1
            setup = LocalContinuationSetup(
                setup_id=(
                    f"LOCAL_CONTINUATION:{self.symbol}:"
                    f"{pending.source_zone.zone_id}:{self._sequence}"
                ),
                side=pending.side,
                source_zone=pending.source_zone,
                impulse_time_ns=pending.decision_bar.ts_close_ns,
                impulse_start_ns=(
                    pending.decision_bar.ts_close_ns
                    - 5 * self.NS_PER_MINUTE
                ),
                impulse_high=pending.decision_bar.high,
                impulse_low=pending.decision_bar.low,
                broken_pivot_id=pending.broken_pivot_id,
                local_pivot_id=pending.local_pivot_id,
                target_zone=target_zone,
                target_price=target_price,
                vwap_numerator=numerator,
                vwap_denominator=denominator,
            )
            self._active = setup
            self.setups.append(setup)
            self._inc("local_continuation_setup_armed")
            self._record(
                "local_continuation_setup_armed",
                time_ns,
                setup_id=setup.setup_id,
                side=setup.side.name,
                source_zone_id=setup.source_zone.zone_id,
                source_lower=setup.source_zone.lower,
                source_upper=setup.source_zone.upper,
                source_invalidation=setup.source_zone.invalidation,
                anchored_vwap=setup.anchored_vwap,
                target_zone_id=target_zone.zone_id,
                target_price=target_price,
                broken_pivot_id=setup.broken_pivot_id,
                local_pivot_id=setup.local_pivot_id,
                constituent_minutes=len(minutes),
                cumulative_signed_taker_quote=cumulative,
                net_price_progress=progress,
                rule_provenance=(
                    LOCAL_NESTED_INITIATIVE_RULE,
                    ANCHORED_FAIR_VALUE_PULLBACK_RULE,
                    LOCAL_CONTINUATION_OBJECTIVE_RULE,
                ),
            )

    def _finish(
        self,
        setup: LocalContinuationSetup,
        reason: str,
        time_ns: int,
        **values: Any,
    ) -> None:
        setup.terminal_reason = reason
        if self._active is setup:
            self._active = None
        self._inc(reason)
        self._record(
            reason,
            time_ns,
            setup_id=setup.setup_id,
            side=setup.side.name,
            source_zone_id=setup.source_zone.zone_id,
            state=setup.state,
            **values,
        )

    @staticmethod
    def _zone_invalidated(
        setup: LocalContinuationSetup,
        bar: Candle,
    ) -> bool:
        return (
            bar.low <= setup.source_zone.invalidation
            if setup.side is Side.LONG
            else bar.high >= setup.source_zone.invalidation
        )

    @staticmethod
    def _target_touched(
        setup: LocalContinuationSetup,
        bar: Candle,
    ) -> bool:
        return (
            bar.high >= setup.target_price
            if setup.side is Side.LONG
            else bar.low <= setup.target_price
        )

    def _update_vwap(
        self,
        setup: LocalContinuationSetup,
        minute: MinuteWeight,
    ) -> None:
        if minute.time_ns <= setup.impulse_time_ns or minute.weight <= 0.0:
            return
        setup.vwap_numerator += minute.typical_price * minute.weight
        setup.vwap_denominator += minute.weight

    def _pullback_choice(
        self,
        setup: LocalContinuationSetup,
        bar: Candle,
    ) -> tuple[LocalContinuationKind, float, float] | None:
        vwap = setup.anchored_vwap
        vwap_lower = vwap - self.tick_size * 0.5
        vwap_upper = vwap + self.tick_size * 0.5
        source_touched = (
            bar.low <= setup.source_zone.upper
            and bar.high >= setup.source_zone.lower
        )
        vwap_touched = bar.low <= vwap_upper and bar.high >= vwap_lower
        if source_touched:
            return (
                LocalContinuationKind.SOURCE_ORDER_BLOCK_PULLBACK,
                setup.source_zone.lower,
                setup.source_zone.upper,
            )
        if vwap_touched:
            return (
                LocalContinuationKind.ANCHORED_VWAP_PULLBACK,
                vwap_lower,
                vwap_upper,
            )
        return None

    def _refresh_target(
        self,
        setup: LocalContinuationSetup,
        bar: Candle,
    ) -> None:
        choices = [
            ("FROZEN", setup.target_zone, setup.target_price),
        ]
        choices.extend(self._structure_targets(setup.side, bar))
        valid = [
            item
            for item in choices
            if (
                item[2] > bar.high
                if setup.side is Side.LONG
                else item[2] < bar.low
            )
        ]
        if not valid:
            return
        _, zone, price = self._nearest(setup.side, valid)
        closer = (
            price < setup.target_price
            if setup.side is Side.LONG
            else price > setup.target_price
        )
        if closer:
            setup.target_zone = zone
            setup.target_price = price
            self._audit(zone)
            self._inc("local_continuation_target_refreshed")

    def _advance_setup(
        self,
        bar: Candle,
        minute: MinuteWeight,
    ) -> list[V5TradePlan]:
        setup = self._active
        if setup is None or bar.ts_close_ns <= setup.impulse_time_ns:
            return []
        self._update_vwap(setup, minute)
        if self._target_touched(setup, bar):
            self._finish(
                setup,
                "local_continuation_target_spent_before_entry",
                bar.ts_close_ns,
            )
            return []
        if self._zone_invalidated(setup, bar):
            self._finish(
                setup,
                "local_continuation_source_invalidated_before_entry",
                bar.ts_close_ns,
            )
            return []

        if setup.state == "WAITING_PULLBACK":
            choice = self._pullback_choice(setup, bar)
            if choice is None:
                return []
            kind, lower, upper = choice
            reacted = (
                bar.close > upper and bar.close > bar.open
                if setup.side is Side.LONG
                else bar.close < lower and bar.close < bar.open
            )
            if not reacted:
                self._finish(
                    setup,
                    "local_continuation_first_pullback_failed",
                    bar.ts_close_ns,
                    pullback_kind=kind.value,
                    pullback_lower=lower,
                    pullback_upper=upper,
                    pullback_open=bar.open,
                    pullback_high=bar.high,
                    pullback_low=bar.low,
                    pullback_close=bar.close,
                )
                return []
            setup.retest_time_ns = bar.ts_close_ns
            setup.retest_high = bar.high
            setup.retest_low = bar.low
            setup.retest_kind = kind
            setup.state = "WAITING_RESPONSE"
            self._inc("local_continuation_pullback_confirmed")
            self._record(
                "local_continuation_pullback_confirmed",
                bar.ts_close_ns,
                setup_id=setup.setup_id,
                side=setup.side.name,
                pullback_kind=kind.value,
                anchored_vwap=setup.anchored_vwap,
                pullback_high=bar.high,
                pullback_low=bar.low,
                pullback_close=bar.close,
                rule_provenance=ANCHORED_FAIR_VALUE_PULLBACK_RULE,
            )
            return []

        if setup.state != "WAITING_RESPONSE":
            return []
        if setup.retest_time_ns is None or bar.ts_close_ns <= setup.retest_time_ns:
            return []
        if setup.retest_low is None or setup.retest_high is None or setup.retest_kind is None:
            raise RuntimeError("local continuation response lost pullback geometry")
        stop = (
            min(setup.retest_low - self.tick_size, setup.source_zone.invalidation)
            if setup.side is Side.LONG
            else max(setup.retest_high + self.tick_size, setup.source_zone.invalidation)
        )
        stop_touched = bar.low <= stop if setup.side is Side.LONG else bar.high >= stop
        if stop_touched:
            self._finish(
                setup,
                "local_continuation_stop_touched_before_response_entry",
                bar.ts_close_ns,
                stop=stop,
            )
            return []
        vwap = setup.anchored_vwap
        control_holds = bar.close > vwap if setup.side is Side.LONG else bar.close < vwap
        response = (
            bar.close > setup.retest_high
            if setup.side is Side.LONG
            else bar.close < setup.retest_low
        )
        if not response or not control_holds:
            self._finish(
                setup,
                "local_continuation_first_response_failed",
                bar.ts_close_ns,
                response_open=bar.open,
                response_high=bar.high,
                response_low=bar.low,
                response_close=bar.close,
                anchored_vwap=vwap,
                control_holds=control_holds,
                rule_provenance=LOCAL_CONTINUATION_RESPONSE_RULE,
            )
            return []

        self._refresh_target(setup, bar)
        entry = bar.close
        target = setup.target_price
        risk = entry - stop if setup.side is Side.LONG else stop - entry
        reward = target - entry if setup.side is Side.LONG else entry - target
        if risk <= 0.0 or reward <= 0.0:
            self._finish(
                setup,
                "local_continuation_nonpositive_preentry_geometry",
                bar.ts_close_ns,
                entry=entry,
                stop=stop,
                target=target,
            )
            return []
        gross_rr = reward / risk
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                "local_continuation_below_minimum_gross_rr",
                bar.ts_close_ns,
                gross_rr=gross_rr,
            )
            return []

        self._sequence += 1
        plan = V5TradePlan(
            plan_id=f"local-continuation-{self.symbol}-{self._sequence:08d}",
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family="NESTED_LOCAL_INITIATIVE_ANCHORED_PULLBACK_RESPONSE",
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
            interaction_time_ns=setup.impulse_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path=ScenarioPath.ACCEPTANCE.value,
            setup_observed_time_ns=setup.source_zone.observed_time_ns,
            trigger_zone_kind=setup.retest_kind.value,
            source_rule_count=4,
            rule_provenance=(
                LOCAL_NESTED_INITIATIVE_RULE,
                ANCHORED_FAIR_VALUE_PULLBACK_RULE,
                LOCAL_CONTINUATION_RESPONSE_RULE,
                LOCAL_CONTINUATION_OBJECTIVE_RULE,
            ),
            scale_name="LOCAL_CONTINUATION",
            higher_timeframe_minutes=15,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        self.plans.append(plan)
        self._finish(
            setup,
            "local_continuation_plan_created",
            bar.ts_close_ns,
            plan_id=plan.plan_id,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            anchored_vwap=vwap,
            pullback_kind=setup.retest_kind.value,
            rule_provenance=(
                LOCAL_NESTED_INITIATIVE_RULE,
                ANCHORED_FAIR_VALUE_PULLBACK_RULE,
                LOCAL_CONTINUATION_RESPONSE_RULE,
                LOCAL_CONTINUATION_OBJECTIVE_RULE,
            ),
        )
        return [plan]

    def on_bar(self, timeframe_minutes: int, bar: Any) -> list[V5TradePlan]:
        if timeframe_minutes == 15:
            self._on_fifteen(bar)
            return []
        if timeframe_minutes == 5:
            self._on_five(bar)
            return []
        if timeframe_minutes != 1:
            return []
        observation = self.flow.observe(bar)
        del observation  # Formation validation reads the completed history.
        minute = self._minute_weight(bar)
        self._minutes.append(minute)
        self._arm_pending(bar.ts_close_ns)
        return self._advance_setup(bar, minute)

    def drain_trace(self) -> list[dict[str, Any]]:
        output, self._trace = self._trace, []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self._zones.get(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._counts.items())),
            "active_setup": None if self._active is None else self._active.setup_id,
            "pending_impulses": sum(len(items) for items in self._pending.values()),
            "local_side": None if self.local_side is None else self.local_side.name,
            "flow": self.flow.diagnostics,
            "rules": (
                LOCAL_NESTED_INITIATIVE_RULE,
                ANCHORED_FAIR_VALUE_PULLBACK_RULE,
                LOCAL_CONTINUATION_RESPONSE_RULE,
                LOCAL_CONTINUATION_OBJECTIVE_RULE,
            ),
        }
