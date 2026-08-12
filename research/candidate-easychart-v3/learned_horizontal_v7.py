"""Crowd-learned horizontal structure and causal Fakeout/Trap execution.

The EasyChart source does not define a numeric price tolerance for the phrase
"everyone sees the same support/resistance". A discretionary trader naturally
uses the rejection area between a wick extreme and its candle body, not a
zero-width floating-point equality. This module translates that ambiguity into
an exact interval-consensus problem:

* every confirmed wick pivot contributes one rejection interval;
* the same physical wick confirmed by multiple pivot spans is one touch;
* a learned boundary exists only when at least two distinct intervals have a
  positive common intersection;
* the maximum-cardinality common intersection owns the new boundary;
* no ATR, percentage, volatility or outcome-derived tolerance is introduced.

A learned boundary then has one state machine. A same-owner-bar excursion and
full reclaim is ``FAKEOUT``. A body close outside is a break attempt; the next
owner candle opening and closing outside is an accepted break. A later reclaim
is ``TRAP_REENTRY`` only after a causal W/M topology is confirmed on the trigger
frame. Reversal entry is the first later retest which closes back inside. The
stop is one tick beyond the complete liquidity episode and the target is the
nearest pre-existing opposite objective supplied by the existing structure
book.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from contracts_v5 import ObjectKind, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_zones import ZoneSide


@dataclass(slots=True)
class DefenseInterval:
    touch_id: str
    side: ZoneSide
    lower: float
    upper: float
    pivot_index: int
    pivot_time_ns: int
    observed_time_ns: int
    pivot_span: int
    strength_ratio: float
    consumed_time_ns: int | None = None

    @property
    def active(self) -> bool:
        return self.consumed_time_ns is None


@dataclass(slots=True)
class LearnedHorizontalZone:
    zone_id: str
    kind: ObjectKind
    family: StructureFamily
    side: ZoneSide
    timeframe_minutes: int
    lower: float
    upper: float
    invalidation: float
    impulse_extreme: float
    formed_index: int
    formed_time_ns: int
    observed_time_ns: int
    formation_indices: tuple[int, ...]
    strength_ratio: float
    source_structure_id: str
    source_pivot_span: int
    touch_count: int
    member_ids: tuple[str, ...]
    first_touch_index: int | None = None
    first_touch_time_ns: int | None = None
    invalidated_index: int | None = None
    invalidated_time_ns: int | None = None
    consumed: bool = False
    consumed_time_ns: int | None = None

    @property
    def active(self) -> bool:
        return self.invalidated_index is None and not self.consumed


class LearnedHorizontalDetector:
    """Confirmed wick-rejection consensus without a distance threshold."""

    def __init__(
        self,
        symbol: str,
        timeframe_minutes: int,
        tick_size: float,
        *,
        pivot_spans: tuple[int, ...] = (2, 6),
    ) -> None:
        if timeframe_minutes <= 0 or tick_size <= 0.0:
            raise ValueError("timeframe and tick size must be positive")
        if not pivot_spans or any(span <= 0 for span in pivot_spans):
            raise ValueError("pivot spans must contain positive integers")
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.tick_size = tick_size
        self.pivot_spans = tuple(sorted(set(pivot_spans)))
        self.bars: list[Candle] = []
        self.intervals: list[DefenseInterval] = []
        self.zones: list[LearnedHorizontalZone] = []
        self._touch_by_key: dict[tuple[ZoneSide, int], DefenseInterval] = {}
        self._active_intervals: dict[str, DefenseInterval] = {}
        self._active_zones: dict[str, LearnedHorizontalZone] = {}
        self._zone_ids: set[str] = set()
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    @staticmethod
    def _body(bar: Candle) -> tuple[float, float]:
        return min(bar.open, bar.close), max(bar.open, bar.close)

    @staticmethod
    def _average_range(items: Iterable[Candle], minimum: float) -> float:
        values = [bar.high - bar.low for bar in items]
        return max(sum(values) / max(len(values), 1), minimum)

    def _observe_existing_intervals(self, bar: Candle) -> None:
        # Strictly beyond the far wick edge spends a defense. An equal touch is
        # precisely the repeated-defense evidence the source describes.
        for touch_id, interval in list(self._active_intervals.items()):
            if bar.ts_close_ns <= interval.observed_time_ns:
                continue
            breached = (
                bar.low < interval.lower
                if interval.side is ZoneSide.SUPPORT
                else bar.high > interval.upper
            )
            if breached:
                interval.consumed_time_ns = bar.ts_close_ns
                self._active_intervals.pop(touch_id, None)
                self._inc("defense_interval_spent")

    def _register_touch(
        self,
        *,
        side: ZoneSide,
        center: int,
        span: int,
        observed_index: int,
        strength: float,
    ) -> DefenseInterval | None:
        key = (side, center)
        existing = self._touch_by_key.get(key)
        if existing is not None:
            # A span-2 and span-6 confirmation of the same candle are one crowd
            # observation, not two touches. Preserve the first causal observed
            # time while allowing later confirmation to strengthen metadata.
            existing.pivot_span = max(existing.pivot_span, span)
            existing.strength_ratio = max(existing.strength_ratio, strength)
            self._inc("same_physical_wick_span_duplicate")
            return None

        pivot = self.bars[center]
        body_low, body_high = self._body(pivot)
        if side is ZoneSide.SUPPORT:
            lower, upper = pivot.low, body_low
            side_name = "SUPPORT"
        else:
            lower, upper = body_high, pivot.high
            side_name = "RESISTANCE"
        if not lower < upper:
            self._inc("wickless_pivot_rejected")
            return None

        touch = DefenseInterval(
            touch_id=(
                f"{self.symbol}:{self.timeframe_minutes}m:DEFENSE_{side_name}:"
                f"{center}"
            ),
            side=side,
            lower=lower,
            upper=upper,
            pivot_index=center,
            pivot_time_ns=pivot.ts_close_ns,
            observed_time_ns=self.bars[observed_index].ts_close_ns,
            pivot_span=span,
            strength_ratio=strength,
        )
        self._touch_by_key[key] = touch
        self._active_intervals[touch.touch_id] = touch
        self.intervals.append(touch)
        self._inc(f"defense_{side_name.lower()}_confirmed")
        return touch

    def _confirm_pivots(self, observed_index: int) -> list[DefenseInterval]:
        created: list[DefenseInterval] = []
        for span in self.pivot_spans:
            center = observed_index - span
            if center < span:
                continue
            window = self.bars[center - span : center + span + 1]
            if len(window) != 2 * span + 1:
                continue
            pivot = self.bars[center]
            highs = [item.high for item in window]
            lows = [item.low for item in window]
            left = window[:span]
            right = window[span + 1 :]
            local_range = self._average_range(left + right, self.tick_size)
            if pivot.low == min(lows) and lows.count(pivot.low) == 1:
                prominence = min(
                    max(item.high for item in left) - pivot.low,
                    max(item.high for item in right) - pivot.low,
                )
                touch = self._register_touch(
                    side=ZoneSide.SUPPORT,
                    center=center,
                    span=span,
                    observed_index=observed_index,
                    strength=prominence / local_range,
                )
                if touch is not None:
                    created.append(touch)
            if pivot.high == max(highs) and highs.count(pivot.high) == 1:
                prominence = min(
                    pivot.high - min(item.low for item in left),
                    pivot.high - min(item.low for item in right),
                )
                touch = self._register_touch(
                    side=ZoneSide.RESISTANCE,
                    center=center,
                    span=span,
                    observed_index=observed_index,
                    strength=prominence / local_range,
                )
                if touch is not None:
                    created.append(touch)
        return created

    def _consensus_members(self, new_touch: DefenseInterval) -> tuple[DefenseInterval, ...]:
        candidates = [
            item
            for item in self._active_intervals.values()
            if item.side is new_touch.side
            and item.observed_time_ns <= new_touch.observed_time_ns
            and max(item.lower, new_touch.lower) < min(item.upper, new_touch.upper)
        ]
        if len(candidates) < 2:
            return ()

        # Every positive-overlap clique occupies at least one open segment
        # between interval endpoints. Evaluate those exact segments; no price
        # bin or tolerance is introduced.
        coordinates = sorted(
            {
                value
                for item in candidates
                for value in (
                    max(item.lower, new_touch.lower),
                    min(item.upper, new_touch.upper),
                )
            },
        )
        best: tuple[DefenseInterval, ...] = ()
        best_key: tuple[Any, ...] | None = None
        seen: set[tuple[str, ...]] = set()
        for left, right in zip(coordinates, coordinates[1:], strict=False):
            if not left < right:
                continue
            point = (left + right) / 2.0
            members = tuple(
                sorted(
                    (item for item in candidates if item.lower < point < item.upper),
                    key=lambda item: (item.pivot_time_ns, item.pivot_index, item.touch_id),
                ),
            )
            member_ids = tuple(item.touch_id for item in members)
            if len(members) < 2 or new_touch.touch_id not in member_ids or member_ids in seen:
                continue
            seen.add(member_ids)
            common_lower = max(item.lower for item in members)
            common_upper = min(item.upper for item in members)
            if not common_lower < common_upper:
                continue
            key = (
                len(members),
                common_upper - common_lower,
                min(item.observed_time_ns for item in members),
                member_ids,
            )
            if best_key is None or key > best_key:
                best_key = key
                best = members
        return best

    def _build_zone(self, new_touch: DefenseInterval) -> LearnedHorizontalZone | None:
        members = self._consensus_members(new_touch)
        if not members:
            return None
        lower = max(item.lower for item in members)
        upper = min(item.upper for item in members)
        formation_indices = tuple(sorted(item.pivot_index for item in members))
        side_name = "SUPPORT" if new_touch.side is ZoneSide.SUPPORT else "RESISTANCE"
        zone_id = (
            f"{self.symbol}:{self.timeframe_minutes}m:LEARNED_HORIZONTAL_{side_name}:"
            + "-".join(str(index) for index in formation_indices)
        )
        if zone_id in self._zone_ids:
            return None

        kind = (
            ObjectKind.HORIZONTAL_SUPPORT
            if new_touch.side is ZoneSide.SUPPORT
            else ObjectKind.HORIZONTAL_RESISTANCE
        )
        member_ids = tuple(item.touch_id for item in members)
        zone = LearnedHorizontalZone(
            zone_id=zone_id,
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=new_touch.side,
            timeframe_minutes=self.timeframe_minutes,
            lower=lower,
            upper=upper,
            invalidation=(
                lower - self.tick_size
                if new_touch.side is ZoneSide.SUPPORT
                else upper + self.tick_size
            ),
            impulse_extreme=lower if new_touch.side is ZoneSide.SUPPORT else upper,
            formed_index=max(formation_indices),
            formed_time_ns=max(item.pivot_time_ns for item in members),
            observed_time_ns=max(item.observed_time_ns for item in members),
            formation_indices=formation_indices,
            strength_ratio=min(item.strength_ratio for item in members),
            source_structure_id=zone_id,
            source_pivot_span=min(item.pivot_span for item in members),
            touch_count=len(members),
            member_ids=member_ids,
        )

        new_member_set = set(member_ids)
        for old_id, old in list(self._active_zones.items()):
            if old.side is not zone.side:
                continue
            old_member_set = set(old.member_ids)
            price_overlap = max(old.lower, zone.lower) < min(old.upper, zone.upper)
            related = bool(old_member_set & new_member_set)
            if old_member_set.issubset(new_member_set) or (price_overlap and related):
                old.consumed = True
                old.consumed_time_ns = zone.observed_time_ns
                self._active_zones.pop(old_id, None)
                self._inc("learned_zone_superseded")

        self._zone_ids.add(zone_id)
        self._active_zones[zone_id] = zone
        self.zones.append(zone)
        self._inc(f"learned_horizontal_{side_name.lower()}_created")
        return zone

    def on_bar(self, bar: Candle) -> list[LearnedHorizontalZone]:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("context bars must arrive in increasing close time")
        self._observe_existing_intervals(bar)
        self.bars.append(bar)
        created_zones: list[LearnedHorizontalZone] = []
        for touch in self._confirm_pivots(len(self.bars) - 1):
            zone = self._build_zone(touch)
            if zone is not None:
                created_zones.append(zone)
        return created_zones

    def consume(self, zone: LearnedHorizontalZone, time_ns: int) -> None:
        if zone.consumed:
            return
        zone.consumed = True
        zone.consumed_time_ns = time_ns
        self._active_zones.pop(zone.zone_id, None)
        self._inc("learned_zone_episode_claimed")

    def active_zones(
        self,
        *,
        side: ZoneSide | None = None,
    ) -> list[LearnedHorizontalZone]:
        return [
            item
            for item in self._active_zones.values()
            if side is None or item.side is side
        ]

    def find_zone(self, zone_id: str) -> LearnedHorizontalZone | None:
        return next((item for item in self.zones if item.zone_id == zone_id), None)


class LearnedSetupState(str, Enum):
    WAITING_NEXT_CONTEXT = "WAITING_NEXT_CONTEXT"
    WAITING_REENTRY = "WAITING_REENTRY"
    REENTRY_PENDING_TOPOLOGY = "REENTRY_PENDING_TOPOLOGY"
    WAITING_RETEST = "WAITING_RETEST"
    PLANNED = "PLANNED"
    ACCEPTED_BREAK = "ACCEPTED_BREAK"
    INVALIDATED = "INVALIDATED"
    TARGET_SPENT = "TARGET_SPENT"
    NO_TARGET = "NO_TARGET"
    NO_TRADE_GEOMETRY = "NO_TRADE_GEOMETRY"
    FIRST_RETEST_UNRESOLVED = "FIRST_RETEST_UNRESOLVED"
    DUPLICATE_EPISODE = "DUPLICATE_EPISODE"
    BOTH_SIDES_UNRESOLVED = "BOTH_SIDES_UNRESOLVED"


@dataclass(slots=True)
class LearnedHorizontalSetup:
    setup_id: str
    zone: LearnedHorizontalZone
    side: Side
    path: str
    state: LearnedSetupState
    interaction_time_ns: int
    interaction_index: int
    interaction_extreme: float
    target_zone: StructureZone | None
    target_price: float | None
    confirmation_time_ns: int | None = None
    reentry_time_ns: int | None = None
    first_retest_consumed: bool = False
    trap_stage: int = 0
    first_external_pivot_time_ns: int | None = None
    middle_pivot_time_ns: int | None = None
    second_external_pivot_time_ns: int | None = None
    topology_confirmed_time_ns: int | None = None
    terminal_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _TriggerPivot:
    side: str
    price: float
    event_time_ns: int
    observed_time_ns: int


class LearnedHorizontalScenarioEngine:
    """Learned boundary -> Fakeout/Trap -> first line retest policy."""

    SOURCE_RULES = (
        "SOURCE_EXPLICIT:REPEATED_DEFENSE_TEACHES_CROWD_A_VISIBLE_BOUNDARY",
        "SOURCE_EXPLICIT:FAKEOUT_IS_ONE_EXCURSION_AND_FAST_RECLAIM",
        "SOURCE_EXPLICIT:TRAP_USES_DELAY_AND_W_OR_M_TOPOLOGY_BEFORE_REENTRY",
        "SOURCE_EXPLICIT:TRUE_BREAK_REQUIRES_BODY_CLOSE_AND_NEXT_OWNER_CANDLE_OUTSIDE",
        "SOURCE_EXPLICIT:CONFIRMATION_ENTRY_USES_RECLAIM_OR_FIRST_RETEST",
        "SOURCE_EXPLICIT:STOP_BEYOND_THE_LIQUIDITY_EPISODE_EXTREME",
        "SOURCE_EXPLICIT:TARGET_THE_FIRST_OPPOSING_STRUCTURE",
    )
    TRANSLATION_RULES = (
        "SOURCE_AMBIGUITY_TRANSLATION:WICK_TO_BODY_IS_THE_DEFENDED_PRICE_INTERVAL",
        "EXTERNAL_METHOD:MAXIMUM_CLIQUE_OF_OVERLAPPING_ONE_DIMENSIONAL_INTERVALS",
        "HUMAN_NATURAL_INFERENCE:MULTIPLE_PIVOT_SPANS_ON_ONE_WICK_ARE_ONE_TOUCH",
        "HUMAN_NATURAL_INFERENCE:FIRST_BOUNDARY_CROSSED_OWNS_A_NESTED_SWEEP",
        "SOURCE_AMBIGUITY_TRANSLATION:OWNER_CLOSE_CLASSIFIES_BOUNDARY_STATE",
        "SOURCE_AMBIGUITY_TRANSLATION:ONE_BAR_TRIGGER_FRACTALS_CAUSALLY_CONFIRM_W_M",
        "SOURCE_AMBIGUITY_TRANSLATION:FIRST_LATER_RETEST_IS_CONSUMED",
    )

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        *,
        scale_name: str,
        context_minutes: int,
        trigger_minutes: int,
        objective_book: Any,
        minimum_gross_rr: float,
    ) -> None:
        if context_minutes <= trigger_minutes:
            raise ValueError("context timeframe must exceed trigger timeframe")
        if tick_size <= 0.0 or minimum_gross_rr <= 0.0:
            raise ValueError("tick size and gross RR must be positive")
        self.symbol = symbol
        self.tick_size = tick_size
        self.scale_name = scale_name
        self.context_minutes = context_minutes
        self.trigger_minutes = trigger_minutes
        self.objective_book = objective_book
        self.minimum_gross_rr = minimum_gross_rr
        self.detector = LearnedHorizontalDetector(symbol, context_minutes, tick_size)
        self.trigger_bars: list[Candle] = []
        self.setups: list[LearnedHorizontalSetup] = []
        self._active: dict[str, LearnedHorizontalSetup] = {}
        self.plans: list[V5TradePlan] = []
        self.audit_zones: list[Any] = []
        self._audit_ids: set[str] = set()
        self.trace_events: list[dict[str, Any]] = []
        self.diagnostics: dict[str, int] = {}
        self.sequence = 0

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _audit(self, zone: Any) -> None:
        zone_id = getattr(zone, "zone_id", None)
        if zone_id and zone_id not in self._audit_ids:
            self._audit_ids.add(zone_id)
            self.audit_zones.append(zone)

    def _trace(
        self,
        kind: str,
        time_ns: int,
        setup: LearnedHorizontalSetup | None = None,
        **values: Any,
    ) -> None:
        event: dict[str, Any] = {
            "scenario_kind": kind,
            "event_time_ns": time_ns,
            "scale_name": self.scale_name,
            "higher_timeframe_minutes": self.context_minutes,
            "decision_timeframe_minutes": self.context_minutes,
            "trigger_timeframe_minutes": self.trigger_minutes,
            **values,
        }
        if setup is not None:
            event.update(
                {
                    "setup_id": setup.setup_id,
                    "setup_state": setup.state.value,
                    "scenario_path": setup.path,
                    "side": setup.side.name,
                    "higher_zone_id": setup.zone.zone_id,
                    "decision_zone_id": setup.zone.zone_id,
                    "overlap_lower": setup.zone.lower,
                    "overlap_upper": setup.zone.upper,
                    "interaction_time_ns": setup.interaction_time_ns,
                    "interaction_extreme": setup.interaction_extreme,
                    "learned_touch_count": setup.zone.touch_count,
                },
            )
        self.trace_events.append(event)

    def drain_trace(self) -> list[dict[str, Any]]:
        output, self.trace_events = self.trace_events, []
        return output

    @staticmethod
    def _trade_side(zone: LearnedHorizontalZone) -> Side:
        return Side.LONG if zone.side is ZoneSide.SUPPORT else Side.SHORT

    @staticmethod
    def _inside(zone: LearnedHorizontalZone, side: Side, close: float) -> bool:
        return close > zone.upper if side is Side.LONG else close < zone.lower

    @staticmethod
    def _outside_open_close(zone: LearnedHorizontalZone, side: Side, bar: Candle) -> bool:
        if side is Side.LONG:
            return bar.open < zone.lower and bar.close < zone.lower
        return bar.open > zone.upper and bar.close > zone.upper

    @staticmethod
    def _swept(zone: LearnedHorizontalZone, bar: Candle) -> bool:
        return bar.low < zone.lower if zone.side is ZoneSide.SUPPORT else bar.high > zone.upper

    @staticmethod
    def _touches(zone: LearnedHorizontalZone, bar: Candle) -> bool:
        return bar.low <= zone.upper and bar.high >= zone.lower

    def _stop_price(self, setup: LearnedHorizontalSetup) -> float:
        return (
            setup.interaction_extreme - self.tick_size
            if setup.side is Side.LONG
            else setup.interaction_extreme + self.tick_size
        )

    def _stop_breached(self, setup: LearnedHorizontalSetup, bar: Candle) -> bool:
        stop = self._stop_price(setup)
        return bar.low <= stop if setup.side is Side.LONG else bar.high >= stop

    def _target_spent(self, setup: LearnedHorizontalSetup, bar: Candle) -> bool:
        if setup.target_price is None:
            return True
        return (
            bar.high >= setup.target_price
            if setup.side is Side.LONG
            else bar.low <= setup.target_price
        )

    def _target_for(
        self,
        zone: LearnedHorizontalZone,
        side: Side,
        bar: Candle,
    ) -> tuple[StructureZone, float] | None:
        return self.objective_book.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=zone.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )

    def _new_setup(
        self,
        zone: LearnedHorizontalZone,
        bar: Candle,
        index: int,
        *,
        fakeout: bool,
    ) -> LearnedHorizontalSetup:
        side = self._trade_side(zone)
        target_result = self._target_for(zone, side, bar)
        target_zone, target_price = (None, None) if target_result is None else target_result
        path = "FAKEOUT" if fakeout else "BREAK_ATTEMPT"
        state = (
            LearnedSetupState.NO_TARGET
            if target_result is None
            else LearnedSetupState.WAITING_RETEST
            if fakeout
            else LearnedSetupState.WAITING_NEXT_CONTEXT
        )
        setup = LearnedHorizontalSetup(
            setup_id=(
                f"{self.scale_name}:LEARNED_HORIZONTAL:{zone.zone_id}:"
                f"{bar.ts_close_ns}"
            ),
            zone=zone,
            side=side,
            path=path,
            state=state,
            interaction_time_ns=bar.ts_close_ns,
            interaction_index=index,
            interaction_extreme=bar.low if side is Side.LONG else bar.high,
            target_zone=target_zone,
            target_price=target_price,
            confirmation_time_ns=bar.ts_close_ns if fakeout else None,
        )
        self.setups.append(setup)
        self.detector.consume(zone, bar.ts_close_ns)
        self._audit(zone)
        if target_zone is not None:
            self._audit(target_zone)
        if state is LearnedSetupState.NO_TARGET:
            setup.terminal_reason = "learned_horizontal_no_preexisting_target"
            self._inc("learned_horizontal_no_preexisting_target")
            self._trace("learned_horizontal_no_preexisting_target", bar.ts_close_ns, setup)
        else:
            self._active[setup.setup_id] = setup
            self._inc("learned_fakeout_created" if fakeout else "learned_break_attempt_created")
            self._trace(
                "learned_fakeout_confirmed" if fakeout else "learned_break_attempt",
                bar.ts_close_ns,
                setup,
                target_zone_id=target_zone.zone_id if target_zone is not None else None,
                target_price=target_price,
            )
        return setup

    def _finish(
        self,
        setup: LearnedHorizontalSetup,
        state: LearnedSetupState,
        time_ns: int,
        reason: str,
        **values: Any,
    ) -> None:
        setup.state = state
        setup.terminal_reason = reason
        self._active.pop(setup.setup_id, None)
        self._inc(reason)
        self._trace(reason, time_ns, setup, **values)

    def _advance_context_setups(self, bar: Candle) -> None:
        for setup in list(self._active.values()):
            if setup.state in {
                LearnedSetupState.WAITING_NEXT_CONTEXT,
                LearnedSetupState.WAITING_REENTRY,
            }:
                if setup.side is Side.LONG:
                    setup.interaction_extreme = min(setup.interaction_extreme, bar.low)
                else:
                    setup.interaction_extreme = max(setup.interaction_extreme, bar.high)

                if setup.state is LearnedSetupState.WAITING_NEXT_CONTEXT and self._outside_open_close(
                    setup.zone,
                    setup.side,
                    bar,
                ):
                    setup.path = "ACCEPTED_BREAK"
                    self._finish(
                        setup,
                        LearnedSetupState.ACCEPTED_BREAK,
                        bar.ts_close_ns,
                        "learned_break_accepted_next_owner",
                    )
                    continue

                if self._inside(setup.zone, setup.side, bar.close):
                    setup.path = "TRAP_REENTRY"
                    setup.reentry_time_ns = bar.ts_close_ns
                    setup.state = LearnedSetupState.REENTRY_PENDING_TOPOLOGY
                    self._inc("learned_trap_reentry_observed")
                    self._trace("learned_trap_reentry_observed", bar.ts_close_ns, setup)
                    if setup.topology_confirmed_time_ns is not None:
                        setup.confirmation_time_ns = max(
                            bar.ts_close_ns,
                            setup.topology_confirmed_time_ns,
                        )
                        setup.state = LearnedSetupState.WAITING_RETEST
                        self._inc("learned_trap_confirmed")
                        self._trace("learned_trap_confirmed", bar.ts_close_ns, setup)
                else:
                    setup.state = LearnedSetupState.WAITING_REENTRY
                continue

            if setup.state in {
                LearnedSetupState.REENTRY_PENDING_TOPOLOGY,
                LearnedSetupState.WAITING_RETEST,
            }:
                if self._stop_breached(setup, bar):
                    self._finish(
                        setup,
                        LearnedSetupState.INVALIDATED,
                        bar.ts_close_ns,
                        "learned_episode_extreme_breached_before_entry",
                    )
                elif self._target_spent(setup, bar):
                    self._finish(
                        setup,
                        LearnedSetupState.TARGET_SPENT,
                        bar.ts_close_ns,
                        "learned_target_spent_before_entry",
                    )

    def _discover_context_interactions(self, bar: Candle, index: int) -> None:
        swept = [zone for zone in self.detector.active_zones() if self._swept(zone, bar)]
        if not swept:
            return
        supports = [zone for zone in swept if zone.side is ZoneSide.SUPPORT]
        resistances = [zone for zone in swept if zone.side is ZoneSide.RESISTANCE]
        if supports and resistances:
            for zone in swept:
                self.detector.consume(zone, bar.ts_close_ns)
                terminal = LearnedHorizontalSetup(
                    setup_id=f"{self.scale_name}:BOTH_SIDES:{zone.zone_id}:{bar.ts_close_ns}",
                    zone=zone,
                    side=self._trade_side(zone),
                    path="BOTH_SIDES_UNRESOLVED",
                    state=LearnedSetupState.BOTH_SIDES_UNRESOLVED,
                    interaction_time_ns=bar.ts_close_ns,
                    interaction_index=index,
                    interaction_extreme=bar.low if zone.side is ZoneSide.SUPPORT else bar.high,
                    target_zone=None,
                    target_price=None,
                    terminal_reason="learned_both_sides_swept_unresolved",
                )
                self.setups.append(terminal)
                self._audit(zone)
            self._inc("learned_both_sides_swept_unresolved")
            self._trace(
                "learned_both_sides_swept_unresolved",
                bar.ts_close_ns,
                None,
                swept_zone_ids=[zone.zone_id for zone in swept],
            )
            return

        # A move crosses the nearest boundary first. Nested levels are one
        # liquidity episode, not several trades.
        if supports:
            selected = max(supports, key=lambda zone: (zone.upper, zone.touch_count, zone.zone_id))
        else:
            selected = min(resistances, key=lambda zone: (zone.lower, -zone.touch_count, zone.zone_id))
        for duplicate in swept:
            if duplicate is selected:
                continue
            self.detector.consume(duplicate, bar.ts_close_ns)
            terminal = LearnedHorizontalSetup(
                setup_id=f"{self.scale_name}:DUPLICATE:{duplicate.zone_id}:{bar.ts_close_ns}",
                zone=duplicate,
                side=self._trade_side(duplicate),
                path="DUPLICATE_EPISODE",
                state=LearnedSetupState.DUPLICATE_EPISODE,
                interaction_time_ns=bar.ts_close_ns,
                interaction_index=index,
                interaction_extreme=bar.low if duplicate.side is ZoneSide.SUPPORT else bar.high,
                target_zone=None,
                target_price=None,
                terminal_reason="learned_nested_sweep_collapsed",
            )
            self.setups.append(terminal)
            self._audit(duplicate)
            self._inc("learned_nested_sweep_collapsed")
            self._trace(
                "learned_nested_sweep_collapsed",
                bar.ts_close_ns,
                terminal,
                selected_zone_id=selected.zone_id,
            )

        side = self._trade_side(selected)
        fakeout = self._inside(selected, side, bar.close)
        self._new_setup(selected, bar, index, fakeout=fakeout)

    def _context_bar(self, bar: Candle) -> list[V5TradePlan]:
        self._advance_context_setups(bar)
        self._discover_context_interactions(bar, len(self.detector.bars))
        for zone in self.detector.on_bar(bar):
            self._audit(zone)
        return []

    def _confirmed_trigger_pivots(self) -> tuple[_TriggerPivot, ...]:
        if len(self.trigger_bars) < 3:
            return ()
        left, center, right = self.trigger_bars[-3:]
        output: list[_TriggerPivot] = []
        if center.low < left.low and center.low < right.low:
            output.append(_TriggerPivot("LOW", center.low, center.ts_close_ns, right.ts_close_ns))
        if center.high > left.high and center.high > right.high:
            output.append(_TriggerPivot("HIGH", center.high, center.ts_close_ns, right.ts_close_ns))
        return tuple(output)

    def _update_trap_topology(self, pivot: _TriggerPivot) -> None:
        for setup in list(self._active.values()):
            if setup.state not in {
                LearnedSetupState.WAITING_NEXT_CONTEXT,
                LearnedSetupState.WAITING_REENTRY,
                LearnedSetupState.REENTRY_PENDING_TOPOLOGY,
            }:
                continue
            if pivot.event_time_ns <= setup.interaction_time_ns:
                continue
            if setup.side is Side.LONG:
                if setup.trap_stage == 0 and pivot.side == "LOW" and pivot.price < setup.zone.lower:
                    setup.first_external_pivot_time_ns = pivot.event_time_ns
                    setup.trap_stage = 1
                elif (
                    setup.trap_stage == 1
                    and pivot.side == "HIGH"
                    and pivot.price > setup.zone.lower
                    and (setup.first_external_pivot_time_ns or 0) < pivot.event_time_ns
                ):
                    setup.middle_pivot_time_ns = pivot.event_time_ns
                    setup.trap_stage = 2
                elif (
                    setup.trap_stage == 2
                    and pivot.side == "LOW"
                    and pivot.price < setup.zone.lower
                    and (setup.middle_pivot_time_ns or 0) < pivot.event_time_ns
                ):
                    setup.second_external_pivot_time_ns = pivot.event_time_ns
                    setup.topology_confirmed_time_ns = pivot.observed_time_ns
                    setup.trap_stage = 3
            else:
                if setup.trap_stage == 0 and pivot.side == "HIGH" and pivot.price > setup.zone.upper:
                    setup.first_external_pivot_time_ns = pivot.event_time_ns
                    setup.trap_stage = 1
                elif (
                    setup.trap_stage == 1
                    and pivot.side == "LOW"
                    and pivot.price < setup.zone.upper
                    and (setup.first_external_pivot_time_ns or 0) < pivot.event_time_ns
                ):
                    setup.middle_pivot_time_ns = pivot.event_time_ns
                    setup.trap_stage = 2
                elif (
                    setup.trap_stage == 2
                    and pivot.side == "HIGH"
                    and pivot.price > setup.zone.upper
                    and (setup.middle_pivot_time_ns or 0) < pivot.event_time_ns
                ):
                    setup.second_external_pivot_time_ns = pivot.event_time_ns
                    setup.topology_confirmed_time_ns = pivot.observed_time_ns
                    setup.trap_stage = 3
            if setup.topology_confirmed_time_ns is not None:
                self._inc("learned_trap_topology_confirmed")
                self._trace(
                    "learned_trap_topology_confirmed",
                    pivot.observed_time_ns,
                    setup,
                    first_external_pivot_time_ns=setup.first_external_pivot_time_ns,
                    middle_pivot_time_ns=setup.middle_pivot_time_ns,
                    second_external_pivot_time_ns=setup.second_external_pivot_time_ns,
                )
                if setup.state is LearnedSetupState.REENTRY_PENDING_TOPOLOGY:
                    if setup.reentry_time_ns is None:
                        raise RuntimeError("trap reentry state lost reentry time")
                    setup.confirmation_time_ns = max(
                        setup.reentry_time_ns,
                        setup.topology_confirmed_time_ns,
                    )
                    setup.state = LearnedSetupState.WAITING_RETEST
                    self._inc("learned_trap_confirmed")
                    self._trace("learned_trap_confirmed", pivot.observed_time_ns, setup)

    def _plan(self, setup: LearnedHorizontalSetup, bar: Candle) -> V5TradePlan | None:
        if setup.target_zone is None or setup.target_price is None:
            self._finish(
                setup,
                LearnedSetupState.NO_TARGET,
                bar.ts_close_ns,
                "learned_plan_lost_target",
            )
            return None
        entry = bar.close
        stop = self._stop_price(setup)
        target = setup.target_price
        valid = stop < entry < target if setup.side is Side.LONG else target < entry < stop
        if not valid:
            self._finish(
                setup,
                LearnedSetupState.NO_TRADE_GEOMETRY,
                bar.ts_close_ns,
                "learned_invalid_preentry_geometry",
                entry=entry,
                stop=stop,
                target=target,
            )
            return None
        gross_rr = abs(target - entry) / abs(entry - stop)
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                LearnedSetupState.NO_TRADE_GEOMETRY,
                bar.ts_close_ns,
                "learned_gross_rr_below_minimum",
                gross_rr=gross_rr,
            )
            return None
        self.sequence += 1
        family = f"{self.scale_name}_LEARNED_HORIZONTAL_{setup.path}_RETEST"
        plan = V5TradePlan(
            plan_id=f"ecv7-lh-{self.scale_name.lower()}-{self.symbol}-{self.sequence:08d}",
            causal_event_id=f"{family}:{setup.setup_id}",
            symbol=self.symbol,
            family=family,
            side=setup.side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=setup.zone.zone_id,
            higher_zone_kind=setup.zone.kind,
            higher_strength_ratio=setup.zone.strength_ratio,
            lower_zone_id=setup.zone.zone_id,
            lower_zone_kind=setup.zone.kind,
            lower_strength_ratio=setup.zone.strength_ratio,
            trigger_zone_id=setup.zone.zone_id,
            trigger_strength_ratio=setup.zone.strength_ratio,
            target_zone_id=setup.target_zone.zone_id,
            target_zone_kind=setup.target_zone.kind,
            overlap_lower=setup.zone.lower,
            overlap_upper=setup.zone.upper,
            interaction_time_ns=setup.interaction_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path=setup.path,
            setup_observed_time_ns=setup.zone.observed_time_ns,
            trigger_zone_kind="LEARNED_HORIZONTAL_FIRST_RETEST",
            source_rule_count=len(self.SOURCE_RULES),
            rule_provenance=self.SOURCE_RULES + self.TRANSLATION_RULES,
            scale_name=self.scale_name,
            higher_timeframe_minutes=self.context_minutes,
            decision_timeframe_minutes=self.context_minutes,
            trigger_timeframe_minutes=self.trigger_minutes,
        )
        setup.state = LearnedSetupState.PLANNED
        self._active.pop(setup.setup_id, None)
        self.plans.append(plan)
        self._inc("learned_horizontal_plan_created")
        self._trace(
            "learned_horizontal_plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            family=family,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
        )
        return plan

    def _advance_trigger_setups(self, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for setup in list(self._active.values()):
            if setup.state is LearnedSetupState.REENTRY_PENDING_TOPOLOGY:
                if self._stop_breached(setup, bar):
                    self._finish(
                        setup,
                        LearnedSetupState.INVALIDATED,
                        bar.ts_close_ns,
                        "learned_episode_extreme_breached_before_topology",
                    )
                elif self._target_spent(setup, bar):
                    self._finish(
                        setup,
                        LearnedSetupState.TARGET_SPENT,
                        bar.ts_close_ns,
                        "learned_target_spent_before_entry",
                    )
                elif (
                    setup.reentry_time_ns is not None
                    and bar.ts_close_ns > setup.reentry_time_ns
                    and self._touches(setup.zone, bar)
                ):
                    self._finish(
                        setup,
                        LearnedSetupState.FIRST_RETEST_UNRESOLVED,
                        bar.ts_close_ns,
                        "learned_trap_first_retest_before_topology",
                    )
                continue

            if setup.state is not LearnedSetupState.WAITING_RETEST:
                continue
            if setup.confirmation_time_ns is None or bar.ts_close_ns <= setup.confirmation_time_ns:
                continue
            if self._stop_breached(setup, bar):
                self._finish(
                    setup,
                    LearnedSetupState.INVALIDATED,
                    bar.ts_close_ns,
                    "learned_episode_extreme_breached_before_retest",
                )
                continue
            if self._target_spent(setup, bar):
                self._finish(
                    setup,
                    LearnedSetupState.TARGET_SPENT,
                    bar.ts_close_ns,
                    "learned_target_spent_before_entry",
                )
                continue
            if not self._touches(setup.zone, bar):
                continue
            if setup.first_retest_consumed:
                raise RuntimeError("learned horizontal first retest processed twice")
            setup.first_retest_consumed = True
            setup.zone.first_touch_index = len(self.trigger_bars) - 1
            setup.zone.first_touch_time_ns = bar.ts_close_ns
            reacted = (
                bar.close > setup.zone.upper and bar.close > bar.open
                if setup.side is Side.LONG
                else bar.close < setup.zone.lower and bar.close < bar.open
            )
            if not reacted:
                self._finish(
                    setup,
                    LearnedSetupState.FIRST_RETEST_UNRESOLVED,
                    bar.ts_close_ns,
                    "learned_first_retest_failed",
                )
                continue
            plan = self._plan(setup, bar)
            if plan is not None:
                output.append(plan)
        return output

    def _trigger_bar(self, bar: Candle) -> list[V5TradePlan]:
        if self.trigger_bars and bar.ts_close_ns <= self.trigger_bars[-1].ts_close_ns:
            raise ValueError("trigger bars must arrive in increasing close time")
        self.trigger_bars.append(bar)
        for pivot in self._confirmed_trigger_pivots():
            self._update_trap_topology(pivot)
        return self._advance_trigger_setups(bar)

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == self.context_minutes:
            return self._context_bar(bar)
        if timeframe_minutes == self.trigger_minutes:
            return self._trigger_bar(bar)
        raise ValueError(f"unsupported learned-horizontal timeframe {timeframe_minutes}")

    def find_zone(self, zone_id: str) -> Any | None:
        zone = self.detector.find_zone(zone_id)
        if zone is not None:
            return zone
        return next((item for item in self.audit_zones if getattr(item, "zone_id", None) == zone_id), None)
