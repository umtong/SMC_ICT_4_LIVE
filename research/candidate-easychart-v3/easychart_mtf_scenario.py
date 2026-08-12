"""EasyChart v3 causal multi-scale scenarios.

The implementation separates the source's roles instead of treating every
chart object as a signal:

* higher/decision OB/FVG overlap supplies location and directional context;
* first interaction is classified as ordinary touch or sweep-and-reclaim;
* a later, event-local, source-sized trigger OB/FVG supplies execution;
* the first later retest is the only entry opportunity;
* stop and pre-existing opposing objective are fixed before submission.

Generic acceptance is deliberately absent here.  The source applies
breakout/acceptance to actual market structures (trendlines, channels and
support/resistance), not to every tiny OB/FVG edge.  That family belongs in the
structure router once those objects are causally encoded.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from domain import Candle, Side
from easychart_zones import (
    EasyChartZoneDetector,
    PriceZone,
    ZoneKind,
    ZoneOverlap,
    ZoneSide,
    overlap_zones,
)
from liquidity import CausalLiquidityDetector, ObjectiveKind, ObjectiveZone


class SetupState(str, Enum):
    WAITING_INTERACTION = "WAITING_INTERACTION"
    REJECTION_WAIT_RECLAIM = "REJECTION_WAIT_RECLAIM"
    WAITING_DISPLACEMENT = "WAITING_DISPLACEMENT"
    WAITING_RETEST = "WAITING_RETEST"
    PLANNED = "PLANNED"
    INVALIDATED = "INVALIDATED"
    TARGET_SPENT = "TARGET_SPENT"
    NO_TRADE_GEOMETRY = "NO_TRADE_GEOMETRY"
    UNRESOLVED = "UNRESOLVED"
    DUPLICATE_EPISODE = "DUPLICATE_EPISODE"


class ScenarioPath(str, Enum):
    TOUCH = "TOUCH"
    REJECTION = "REJECTION"


SOURCE_EXPLICIT_RULES = (
    "SOURCE_EXPLICIT:OB_FVG_REQUIRE_MEANINGFUL_STRUCTURE",
    "SOURCE_EXPLICIT:FVG_MIDDLE_DISPLACEMENT_AND_OB_SIZE_MATTER",
    "SOURCE_EXPLICIT:FAKEOUT_RECLAIM_AND_RETEST",
    "SOURCE_EXPLICIT:NO_ENTRY_WHEN_PLANNED_ZONE_IS_NOT_RETESTED",
    "SOURCE_EXPLICIT:STOP_BEYOND_CAUSAL_INVALIDATION",
    "SOURCE_EXPLICIT:TARGET_PREEXISTING_OPPOSITE_STRUCTURE_OR_SWING",
)

TRANSLATION_RULES = (
    "HUMAN_NATURAL_INFERENCE:ZONE_BAND_HAS_NEAR_AND_FAR_EDGES",
    "HUMAN_NATURAL_INFERENCE:FIRST_RETEST_IS_CONSUMED_EVEN_IF_REACTION_FAILS",
    "RESEARCH_HYPOTHESIS:MACRO_60_15_5_AND_MICRO_15_5_1_STACKS",
    "RESEARCH_HYPOTHESIS:EXACT_CROSS_TIMEFRAME_ZONE_INTERSECTION_IS_CONTEXT",
    "RESEARCH_HYPOTHESIS:CONFIRMED_WICK_PIVOTS_ARE_LIQUIDITY_OBJECTIVES",
)


@dataclass(slots=True)
class ScaleSetup:
    setup_id: str
    scale_name: str
    overlap: ZoneOverlap
    higher_zone: PriceZone
    lower_zone: PriceZone
    observed_time_ns: int
    state: SetupState = SetupState.WAITING_INTERACTION
    path: ScenarioPath | None = None
    interaction_time_ns: int | None = None
    interaction_trigger_index: int | None = None
    interaction_extreme: float | None = None
    confirmation_time_ns: int | None = None
    trigger_zone_id: str | None = None
    trigger_zone: PriceZone | None = None
    trigger_time_ns: int | None = None
    trigger_index: int | None = None


TargetKind = ZoneKind | ObjectiveKind
TargetZone = PriceZone | ObjectiveZone


@dataclass(frozen=True, slots=True)
class MTFTradePlan:
    plan_id: str
    causal_event_id: str
    symbol: str
    family: str
    side: Side
    observed_time_ns: int
    entry: float
    stop: float
    target: float
    gross_rr: float
    setup_id: str
    higher_zone_id: str
    higher_zone_kind: ZoneKind
    higher_strength_ratio: float
    lower_zone_id: str
    lower_zone_kind: ZoneKind
    lower_strength_ratio: float
    trigger_zone_id: str
    trigger_strength_ratio: float
    target_zone_id: str
    target_zone_kind: TargetKind
    overlap_lower: float
    overlap_upper: float
    interaction_time_ns: int
    trigger_time_ns: int
    scenario_path: str
    setup_observed_time_ns: int
    trigger_zone_kind: str
    source_rule_count: int
    rule_provenance: tuple[str, ...]
    scale_name: str
    higher_timeframe_minutes: int
    decision_timeframe_minutes: int
    trigger_timeframe_minutes: int

    @property
    def kind_diversity(self) -> int:
        return len({self.higher_zone_kind, self.lower_zone_kind, self.target_zone_kind})


class ScaleScenarioEngine:
    """One EasyChart context/decision/trigger hierarchy for one instrument."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        *,
        scale_name: str = "MACRO",
        higher_minutes: int = 60,
        decision_minutes: int = 15,
        trigger_minutes: int = 5,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        if not higher_minutes > decision_minutes > trigger_minutes > 0:
            raise ValueError("timeframes must satisfy higher > decision > trigger > 0")
        if tick_size <= 0.0 or minimum_gross_rr <= 0.0:
            raise ValueError("tick size and minimum gross RR must be positive")
        self.symbol = symbol
        self.tick_size = tick_size
        self.scale_name = scale_name
        self.higher_minutes = higher_minutes
        self.decision_minutes = decision_minutes
        self.trigger_minutes = trigger_minutes
        self.minimum_gross_rr = minimum_gross_rr
        self.detectors = {
            higher_minutes: EasyChartZoneDetector(symbol, higher_minutes, tick_size),
            decision_minutes: EasyChartZoneDetector(symbol, decision_minutes, tick_size),
            trigger_minutes: EasyChartZoneDetector(symbol, trigger_minutes, tick_size),
        }
        self.objectives = {
            higher_minutes: CausalLiquidityDetector(symbol, higher_minutes, tick_size),
            decision_minutes: CausalLiquidityDetector(symbol, decision_minutes, tick_size),
        }
        self.setups: list[ScaleSetup] = []
        self.plans: list[MTFTradePlan] = []
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}
        self.trace_events: list[dict[str, Any]] = []

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _trace(self, kind: str, event_time_ns: int, setup: ScaleSetup | None = None, **values: Any) -> None:
        event: dict[str, Any] = {
            "scenario_kind": kind,
            "event_time_ns": event_time_ns,
            "scale_name": self.scale_name,
            "higher_timeframe_minutes": self.higher_minutes,
            "decision_timeframe_minutes": self.decision_minutes,
            "trigger_timeframe_minutes": self.trigger_minutes,
            **values,
        }
        if setup is not None:
            event.update(
                {
                    "setup_id": setup.setup_id,
                    "setup_state": setup.state.value,
                    "scenario_path": None if setup.path is None else setup.path.value,
                    "overlap_lower": setup.overlap.lower,
                    "overlap_upper": setup.overlap.upper,
                    "higher_zone_id": setup.higher_zone.zone_id,
                    "decision_zone_id": setup.lower_zone.zone_id,
                },
            )
        self.trace_events.append(event)

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.trace_events
        self.trace_events = []
        return output

    @staticmethod
    def _terminal(state: SetupState) -> bool:
        return state in {
            SetupState.PLANNED,
            SetupState.INVALIDATED,
            SetupState.TARGET_SPENT,
            SetupState.NO_TRADE_GEOMETRY,
            SetupState.UNRESOLVED,
            SetupState.DUPLICATE_EPISODE,
        }

    @staticmethod
    def _bar_touches(bar: Candle, lower: float, upper: float) -> bool:
        return bar.low <= upper and bar.high >= lower

    @staticmethod
    def _side_for_context(side: ZoneSide) -> Side:
        return Side.LONG if side is ZoneSide.SUPPORT else Side.SHORT

    def _setup_id(self, overlap: ZoneOverlap) -> str:
        return f"{self.scale_name}:SETUP:{overlap.overlap_id}"

    def _refresh_setups(self, event_time_ns: int) -> None:
        existing = {setup.setup_id for setup in self.setups}
        higher_detector = self.detectors[self.higher_minutes]
        decision_detector = self.detectors[self.decision_minutes]
        for higher in higher_detector.active_zones():
            for decision in decision_detector.active_zones():
                if not (higher.high_quality_by_size or decision.high_quality_by_size):
                    continue
                if higher.first_touch_index is not None or decision.first_touch_index is not None:
                    continue
                overlap = overlap_zones(higher, decision)
                if overlap is None:
                    continue
                setup_id = self._setup_id(overlap)
                if setup_id in existing:
                    continue
                setup = ScaleSetup(
                    setup_id=setup_id,
                    scale_name=self.scale_name,
                    overlap=overlap,
                    higher_zone=higher,
                    lower_zone=decision,
                    observed_time_ns=overlap.observed_time_ns,
                )
                self.setups.append(setup)
                existing.add(setup_id)
                self._inc("setup_created")
                self._inc(f"setup_{higher.kind.value.lower()}_{decision.kind.value.lower()}")
                self._trace("setup_created", event_time_ns, setup)

    def _finish(self, setup: ScaleSetup, state: SetupState, bar: Candle, reason: str, **values: Any) -> None:
        setup.state = state
        setup.higher_zone.consumed = True
        setup.lower_zone.consumed = True
        if setup.trigger_zone is not None:
            setup.trigger_zone.consumed = True
        self._inc(reason)
        self._trace(reason, bar.ts_close_ns, setup, **values)

    def _context_still_available_before_interaction(self, setup: ScaleSetup) -> bool:
        return setup.higher_zone.active and setup.lower_zone.active

    def _interaction_key(self, setup: ScaleSetup) -> tuple[int, float, float, str]:
        diversity = len({setup.higher_zone.kind, setup.lower_zone.kind})
        strength = min(setup.higher_zone.strength_ratio, setup.lower_zone.strength_ratio)
        width = setup.overlap.upper - setup.overlap.lower
        return (-diversity, -strength, width, setup.setup_id)

    def _classify_new_interactions(self, bar: Candle, index: int) -> None:
        if index <= 0:
            return
        previous = self.detectors[self.trigger_minutes].bars[index - 1]
        candidates: dict[tuple[ZoneSide, ScenarioPath], list[ScaleSetup]] = {}
        for setup in self.setups:
            if setup.state is not SetupState.WAITING_INTERACTION:
                continue
            if bar.ts_close_ns <= setup.observed_time_ns:
                continue
            if not self._context_still_available_before_interaction(setup):
                self._finish(setup, SetupState.INVALIDATED, bar, "context_spent_before_interaction")
                continue
            side = setup.overlap.side
            if side is ZoneSide.SUPPORT:
                outside = bar.low < setup.overlap.lower
                first_touch = previous.close > setup.overlap.upper and bar.low <= setup.overlap.upper
                stayed_usable = bar.close >= setup.overlap.lower
            else:
                outside = bar.high > setup.overlap.upper
                first_touch = previous.close < setup.overlap.lower and bar.high >= setup.overlap.lower
                stayed_usable = bar.close <= setup.overlap.upper
            if outside:
                path = ScenarioPath.REJECTION
            elif first_touch and stayed_usable:
                path = ScenarioPath.TOUCH
            else:
                continue
            candidates.setdefault((side, path), []).append(setup)

        for (_, path), group in candidates.items():
            selected = sorted(group, key=self._interaction_key)[0]
            for duplicate in group:
                if duplicate is selected:
                    continue
                if max(duplicate.overlap.lower, selected.overlap.lower) <= min(
                    duplicate.overlap.upper,
                    selected.overlap.upper,
                ):
                    self._finish(
                        duplicate,
                        SetupState.DUPLICATE_EPISODE,
                        bar,
                        "nested_context_collapsed",
                        selected_setup_id=selected.setup_id,
                    )
            selected.path = path
            selected.interaction_time_ns = bar.ts_close_ns
            selected.interaction_trigger_index = index
            selected.interaction_extreme = bar.low if selected.overlap.side is ZoneSide.SUPPORT else bar.high
            if path is ScenarioPath.TOUCH:
                selected.confirmation_time_ns = bar.ts_close_ns
                selected.state = SetupState.WAITING_DISPLACEMENT
                self._inc("touch_context_confirmed")
                self._trace("touch_context_confirmed", bar.ts_close_ns, selected)
            else:
                reclaimed = (
                    bar.close >= selected.overlap.upper
                    if selected.overlap.side is ZoneSide.SUPPORT
                    else bar.close <= selected.overlap.lower
                )
                if reclaimed:
                    selected.confirmation_time_ns = bar.ts_close_ns
                    selected.state = SetupState.WAITING_DISPLACEMENT
                    self._inc("rejection_reclaim_confirmed")
                    self._trace("rejection_reclaim_confirmed", bar.ts_close_ns, selected)
                else:
                    selected.state = SetupState.REJECTION_WAIT_RECLAIM
                    self._inc("rejection_excursion_unresolved")
                    self._trace("rejection_excursion_unresolved", bar.ts_close_ns, selected)

    def _advance_reclaims(self, bar: Candle) -> None:
        for setup in self.setups:
            if setup.state is not SetupState.REJECTION_WAIT_RECLAIM:
                continue
            if setup.interaction_extreme is None:
                raise RuntimeError("rejection setup lost interaction extreme")
            if setup.overlap.side is ZoneSide.SUPPORT:
                setup.interaction_extreme = min(setup.interaction_extreme, bar.low)
                reclaimed = bar.close >= setup.overlap.upper
            else:
                setup.interaction_extreme = max(setup.interaction_extreme, bar.high)
                reclaimed = bar.close <= setup.overlap.lower
            if reclaimed:
                setup.confirmation_time_ns = bar.ts_close_ns
                setup.state = SetupState.WAITING_DISPLACEMENT
                self._inc("rejection_reclaim_confirmed")
                self._trace("rejection_reclaim_confirmed", bar.ts_close_ns, setup)

    def _trigger_formation_touched_context(self, trigger: PriceZone, setup: ScaleSetup) -> bool:
        detector = self.detectors[self.trigger_minutes]
        return any(
            0 <= index < len(detector.bars)
            and self._bar_touches(detector.bars[index], setup.overlap.lower, setup.overlap.upper)
            for index in trigger.formation_indices
        )

    def _event_local_trigger(self, setup: ScaleSetup, created: Iterable[PriceZone]) -> PriceZone | None:
        confirmation_time = setup.confirmation_time_ns or 0
        candidates = [
            zone
            for zone in created
            if zone.side is setup.overlap.side
            and zone.observed_time_ns > confirmation_time
            and zone.high_quality_by_size
            and self._trigger_formation_touched_context(zone, setup)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda zone: (zone.observed_time_ns, zone.formed_index, zone.zone_id))
        chosen = candidates[0]
        if any(
            other is not chosen and other.kind is not chosen.kind and chosen.overlaps(other)
            for other in candidates
        ):
            self._inc("event_local_ob_fvg_confluence")
        return chosen

    def _sweep_extreme_breached(self, setup: ScaleSetup, bar: Candle) -> bool:
        if setup.path is not ScenarioPath.REJECTION or setup.interaction_extreme is None:
            return False
        if setup.overlap.side is ZoneSide.SUPPORT:
            return bar.low <= setup.interaction_extreme - self.tick_size
        return bar.high >= setup.interaction_extreme + self.tick_size

    def _advance_displacement(self, bar: Candle, index: int, created: list[PriceZone]) -> None:
        for setup in self.setups:
            if setup.state is not SetupState.WAITING_DISPLACEMENT:
                continue
            if self._sweep_extreme_breached(setup, bar):
                self._finish(setup, SetupState.INVALIDATED, bar, "rejection_extreme_breached")
                continue
            if setup.path is ScenarioPath.TOUCH:
                crossed_far_edge = (
                    bar.close < setup.overlap.lower
                    if setup.overlap.side is ZoneSide.SUPPORT
                    else bar.close > setup.overlap.upper
                )
                if crossed_far_edge:
                    self._finish(setup, SetupState.UNRESOLVED, bar, "touch_became_unresolved_break")
                    continue
            trigger = self._event_local_trigger(setup, created)
            if trigger is None:
                continue
            setup.trigger_zone = trigger
            setup.trigger_zone_id = trigger.zone_id
            setup.trigger_time_ns = trigger.observed_time_ns
            setup.trigger_index = index
            setup.state = SetupState.WAITING_RETEST
            self._inc("event_local_displacement_confirmed")
            self._trace(
                "event_local_displacement_confirmed",
                bar.ts_close_ns,
                setup,
                trigger_zone_id=trigger.zone_id,
                trigger_zone_kind=trigger.kind.value,
                trigger_strength_ratio=trigger.strength_ratio,
            )

    def _all_targets(self, wanted: ZoneSide) -> Iterable[TargetZone]:
        for timeframe in (self.higher_minutes, self.decision_minutes):
            yield from self.detectors[timeframe].active_zones(side=wanted)
            yield from self.objectives[timeframe].active_zones(side=wanted)

    def _opposite_target(
        self,
        side: Side,
        entry: float,
        current: Candle,
        causal_cutoff_ns: int,
    ) -> tuple[TargetZone, float] | None:
        wanted = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        candidates: list[tuple[float, TargetZone]] = []
        for zone in self._all_targets(wanted):
            if zone.observed_time_ns >= causal_cutoff_ns:
                continue
            if side is Side.LONG:
                price = zone.lower
                if price > max(entry, current.high):
                    candidates.append((price, zone))
            else:
                price = zone.upper
                if price < min(entry, current.low):
                    candidates.append((price, zone))
        if not candidates:
            return None
        if side is Side.LONG:
            price, zone = min(candidates, key=lambda item: (item[0], item[1].observed_time_ns))
        else:
            price, zone = max(candidates, key=lambda item: (item[0], -item[1].observed_time_ns))
        return zone, price

    def _make_plan(self, setup: ScaleSetup, bar: Candle, entry: float, stop: float) -> MTFTradePlan | None:
        if setup.path is None or setup.interaction_time_ns is None or setup.trigger_zone is None:
            raise RuntimeError("plan attempted from incomplete setup")
        side = self._side_for_context(setup.overlap.side)
        if side is Side.LONG and not stop < entry:
            self._finish(setup, SetupState.NO_TRADE_GEOMETRY, bar, "invalid_long_geometry")
            return None
        if side is Side.SHORT and not entry < stop:
            self._finish(setup, SetupState.NO_TRADE_GEOMETRY, bar, "invalid_short_geometry")
            return None
        target_result = self._opposite_target(side, entry, bar, setup.interaction_time_ns)
        if target_result is None:
            self._finish(setup, SetupState.TARGET_SPENT, bar, "no_unspent_preexisting_target")
            return None
        target_zone, target = target_result
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0.0 or reward <= 0.0:
            self._finish(setup, SetupState.NO_TRADE_GEOMETRY, bar, "nonpositive_geometry")
            return None
        gross_rr = reward / risk
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                SetupState.NO_TRADE_GEOMETRY,
                bar,
                "gross_rr_below_minimum",
                gross_rr=gross_rr,
            )
            return None

        self.sequence += 1
        mechanism = (
            "SWEEP_RECLAIM_DISPLACEMENT_RETEST"
            if setup.path is ScenarioPath.REJECTION
            else "CONFLUENCE_TOUCH_DISPLACEMENT_RETEST"
        )
        family = f"{self.scale_name}_{mechanism}"
        causal_event_id = f"{family}:{setup.setup_id}:{setup.interaction_time_ns}:{setup.trigger_zone.zone_id}"
        plan = MTFTradePlan(
            plan_id=f"ecv3-{self.scale_name.lower()}-{self.symbol}-{self.sequence:08d}",
            causal_event_id=causal_event_id,
            symbol=self.symbol,
            family=family,
            side=side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=setup.higher_zone.zone_id,
            higher_zone_kind=setup.higher_zone.kind,
            higher_strength_ratio=setup.higher_zone.strength_ratio,
            lower_zone_id=setup.lower_zone.zone_id,
            lower_zone_kind=setup.lower_zone.kind,
            lower_strength_ratio=setup.lower_zone.strength_ratio,
            trigger_zone_id=setup.trigger_zone.zone_id,
            trigger_strength_ratio=setup.trigger_zone.strength_ratio,
            target_zone_id=target_zone.zone_id,
            target_zone_kind=target_zone.kind,
            overlap_lower=setup.overlap.lower,
            overlap_upper=setup.overlap.upper,
            interaction_time_ns=setup.interaction_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path=setup.path.value,
            setup_observed_time_ns=setup.observed_time_ns,
            trigger_zone_kind=setup.trigger_zone.kind.value,
            source_rule_count=len(SOURCE_EXPLICIT_RULES),
            rule_provenance=SOURCE_EXPLICIT_RULES + TRANSLATION_RULES,
            scale_name=self.scale_name,
            higher_timeframe_minutes=self.higher_minutes,
            decision_timeframe_minutes=self.decision_minutes,
            trigger_timeframe_minutes=self.trigger_minutes,
        )
        setup.state = SetupState.PLANNED
        setup.higher_zone.consumed = True
        setup.lower_zone.consumed = True
        setup.trigger_zone.consumed = True
        self.plans.append(plan)
        self._inc("plan_created")
        self._inc(f"plan_{setup.path.value.lower()}")
        self._trace(
            "plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            family=family,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            target_zone_kind=target_zone.kind.value,
        )
        return plan

    def _advance_retests(self, bar: Candle, index: int) -> list[MTFTradePlan]:
        plans: list[MTFTradePlan] = []
        for setup in self.setups:
            if setup.state is not SetupState.WAITING_RETEST:
                continue
            if self._sweep_extreme_breached(setup, bar):
                self._finish(setup, SetupState.INVALIDATED, bar, "rejection_extreme_breached")
                continue
            trigger = setup.trigger_zone
            trigger_index = setup.trigger_index
            if trigger is None or trigger_index is None:
                raise RuntimeError("retest setup lost trigger")
            if index <= trigger_index or not self._bar_touches(bar, trigger.lower, trigger.upper):
                continue
            if setup.overlap.side is ZoneSide.SUPPORT:
                reacted = bar.close > trigger.upper and bar.close > bar.open
                interaction_stop = (setup.interaction_extreme or bar.low) - self.tick_size
                stop = min(interaction_stop, trigger.invalidation)
            else:
                reacted = bar.close < trigger.lower and bar.close < bar.open
                interaction_stop = (setup.interaction_extreme or bar.high) + self.tick_size
                stop = max(interaction_stop, trigger.invalidation)
            if not reacted:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar,
                    "first_retest_failed_reaction",
                    trigger_zone_id=trigger.zone_id,
                )
                continue
            plan = self._make_plan(setup, bar, bar.close, stop)
            if plan is not None:
                plans.append(plan)
        return plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        if timeframe_minutes not in self.detectors:
            raise ValueError(f"unsupported timeframe for {self.scale_name}: {timeframe_minutes}")
        for objective_timeframe, objective in self.objectives.items():
            if timeframe_minutes == objective_timeframe:
                objective.on_bar(bar)
            else:
                objective.observe_price(bar)
        detector = self.detectors[timeframe_minutes]
        created = detector.on_bar(bar)
        if timeframe_minutes in (self.higher_minutes, self.decision_minutes):
            self._refresh_setups(bar.ts_close_ns)
            return []
        index = len(detector.bars) - 1
        self._advance_reclaims(bar)
        self._classify_new_interactions(bar, index)
        self._advance_displacement(bar, index, created)
        return self._advance_retests(bar, index)

    def find_zone(self, zone_id: str) -> TargetZone | None:
        for detector in self.detectors.values():
            for zone in detector.zones:
                if zone.zone_id == zone_id:
                    return zone
        for detector in self.objectives.values():
            for zone in detector.zones:
                if zone.zone_id == zone_id:
                    return zone
        return None


class MultiScaleScenarioBundle:
    """One symbol, two source-motivated decision scales, one plan stream."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.macro = ScaleScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO",
            higher_minutes=60,
            decision_minutes=15,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = ScaleScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = {
            60: self.macro.detectors[60],
            15: self.macro.detectors[15],
            5: self.macro.detectors[5],
        }

    @property
    def setups(self) -> list[ScaleSetup]:
        return self.macro.setups + self.micro.setups

    @property
    def plans(self) -> list[MTFTradePlan]:
        return self.macro.plans + self.micro.plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {"macro": self.macro.diagnostics, "micro": self.micro.diagnostics}

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        plans: list[MTFTradePlan] = []
        if timeframe_minutes in self.macro.detectors:
            plans.extend(self.macro.on_bar(timeframe_minutes, bar))
        if timeframe_minutes in self.micro.detectors:
            plans.extend(self.micro.on_bar(timeframe_minutes, bar))
        return sorted(
            plans,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        return self.macro.drain_trace() + self.micro.drain_trace()

    def find_zone(self, zone_id: str) -> TargetZone | None:
        return self.macro.find_zone(zone_id) or self.micro.find_zone(zone_id)


MTFOverlapScenarioEngine = MultiScaleScenarioBundle
