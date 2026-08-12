"""EasyChart v3 causal multi-timeframe auction scenarios.

The source material does not describe OB/FVG as unconditional entries. It
first establishes a meaningful structure, observes how price interacts with
its liquidity, and only then uses a lower-timeframe structure to execute. This
module encodes that decision order as two mutually exclusive paths:

REJECTION
    60m/15m same-side context -> excursion outside the context -> reclaim ->
    later event-local 5m OB/FVG displacement -> first retest -> trade.

ACCEPTANCE
    60m/15m context -> 15m close outside -> next 15m bar opens and closes
    outside -> first 5m S/R-flip retest -> trade.

A setup may also remain UNRESOLVED. No actor intent is inferred; every state
transition is defined by observations available at that close timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from domain import Candle, Side
from easychart_zones import (
    EasyChartZoneDetector,
    PriceZone,
    ZoneKind,
    ZoneOverlap,
    ZoneSide,
    overlap_zones,
)


class SetupState(str, Enum):
    WAITING_INTERACTION = "WAITING_INTERACTION"
    REJECTION_WAIT_CONFIRM = "REJECTION_WAIT_CONFIRM"
    REJECTION_WAIT_DISPLACEMENT = "REJECTION_WAIT_DISPLACEMENT"
    REJECTION_WAIT_RETEST = "REJECTION_WAIT_RETEST"
    ACCEPTANCE_WAIT_HOLD = "ACCEPTANCE_WAIT_HOLD"
    ACCEPTANCE_WAIT_RETEST = "ACCEPTANCE_WAIT_RETEST"
    PLANNED = "PLANNED"
    INVALIDATED = "INVALIDATED"
    TARGET_SPENT = "TARGET_SPENT"
    NO_TRADE_GEOMETRY = "NO_TRADE_GEOMETRY"
    UNRESOLVED = "UNRESOLVED"


class ScenarioPath(str, Enum):
    REJECTION = "REJECTION"
    ACCEPTANCE = "ACCEPTANCE"


SOURCE_EXPLICIT_RULES = (
    "SOURCE_EXPLICIT:OB_FVG_REQUIRE_MEANINGFUL_STRUCTURE",
    "SOURCE_EXPLICIT:FAKEOUT_RECLAIM_AND_RETEST",
    "SOURCE_EXPLICIT:NO_ENTRY_WHEN_PLANNED_ZONE_IS_NOT_RETESTED",
    "SOURCE_EXPLICIT:STOP_BEYOND_CAUSAL_INVALIDATION",
    "SOURCE_EXPLICIT:TARGET_PREEXISTING_OPPOSITE_STRUCTURE",
)

TRANSLATION_RULES = (
    "HUMAN_NATURAL_INFERENCE:ZONE_BAND_HAS_INSIDE_AND_OUTSIDE_EDGES",
    "RESEARCH_HYPOTHESIS:60M_15M_CONTEXT_AND_5M_EXECUTION",
    "RESEARCH_HYPOTHESIS:EXACT_CROSS_TIMEFRAME_ZONE_INTERSECTION_IS_CONTEXT",
)


@dataclass(slots=True)
class MTFSetup:
    setup_id: str
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
    acceptance_break_index: int | None = None
    acceptance_break_time_ns: int | None = None
    acceptance_break_extreme: float | None = None
    trigger_zone_id: str | None = None
    trigger_zone: PriceZone | None = None
    trigger_time_ns: int | None = None
    trigger_index: int | None = None


@dataclass(frozen=True, slots=True)
class MTFTradePlan:
    # Keep the v2 evidence contract so the mature Nautilus reporting path can
    # be reused without rebuilding accounting or execution infrastructure.
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
    target_zone_kind: ZoneKind
    overlap_lower: float
    overlap_upper: float
    interaction_time_ns: int
    trigger_time_ns: int
    scenario_path: str
    setup_observed_time_ns: int
    trigger_zone_kind: str
    source_rule_count: int
    rule_provenance: tuple[str, ...]

    @property
    def kind_diversity(self) -> int:
        return len({self.higher_zone_kind, self.lower_zone_kind, self.target_zone_kind})


class MTFOverlapScenarioEngine:
    """Causal EasyChart decision engine for one instrument."""

    REJECTION_FAMILY = "SWEEP_RECLAIM_DISPLACEMENT_RETEST"
    ACCEPTANCE_FAMILY = "ACCEPTANCE_HOLD_FLIP_RETEST"

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        *,
        higher_minutes: int = 60,
        decision_minutes: int = 15,
        trigger_minutes: int = 5,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        if not higher_minutes > decision_minutes > trigger_minutes > 0:
            raise ValueError("timeframes must satisfy higher > decision > trigger > 0")
        if tick_size <= 0.0:
            raise ValueError("tick_size must be positive")
        if minimum_gross_rr <= 0.0:
            raise ValueError("minimum_gross_rr must be positive")
        self.symbol = symbol
        self.tick_size = tick_size
        self.higher_minutes = higher_minutes
        self.decision_minutes = decision_minutes
        self.trigger_minutes = trigger_minutes
        self.minimum_gross_rr = minimum_gross_rr
        self.detectors = {
            higher_minutes: EasyChartZoneDetector(symbol, higher_minutes, tick_size),
            decision_minutes: EasyChartZoneDetector(symbol, decision_minutes, tick_size),
            trigger_minutes: EasyChartZoneDetector(symbol, trigger_minutes, tick_size),
        }
        self.setups: list[MTFSetup] = []
        self.plans: list[MTFTradePlan] = []
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}
        self.trace_events: list[dict[str, Any]] = []

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _trace(self, kind: str, event_time_ns: int, setup: MTFSetup | None = None, **values: Any) -> None:
        event: dict[str, Any] = {
            "scenario_kind": kind,
            "event_time_ns": event_time_ns,
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
        drained = self.trace_events
        self.trace_events = []
        return drained

    @staticmethod
    def _bar_touches_zone(bar: Candle, lower: float, upper: float) -> bool:
        return bar.low <= upper and bar.high >= lower

    @staticmethod
    def _setup_id(overlap: ZoneOverlap) -> str:
        return f"SETUP:{overlap.overlap_id}"

    @staticmethod
    def _terminal(state: SetupState) -> bool:
        return state in {
            SetupState.PLANNED,
            SetupState.INVALIDATED,
            SetupState.TARGET_SPENT,
            SetupState.NO_TRADE_GEOMETRY,
            SetupState.UNRESOLVED,
        }

    @staticmethod
    def _side_for_context(zone_side: ZoneSide, path: ScenarioPath) -> Side:
        if path is ScenarioPath.REJECTION:
            return Side.LONG if zone_side is ZoneSide.SUPPORT else Side.SHORT
        return Side.SHORT if zone_side is ZoneSide.SUPPORT else Side.LONG

    def _refresh_setups(self, event_time_ns: int) -> None:
        higher_detector = self.detectors[self.higher_minutes]
        decision_detector = self.detectors[self.decision_minutes]
        existing = {setup.setup_id for setup in self.setups}
        for higher in higher_detector.active_zones():
            for decision in decision_detector.active_zones():
                # FVGs are already rejected below the source-stated 2x body
                # expansion. For OBs, at least one member must carry that
                # explicit size evidence; the other contributes location.
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
                setup = MTFSetup(
                    setup_id=setup_id,
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

    def _opposite_target(
        self,
        side: Side,
        entry: float,
        current: Candle,
        causal_cutoff_ns: int,
    ) -> tuple[PriceZone, float] | None:
        wanted = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        candidates: list[tuple[float, PriceZone]] = []
        for timeframe in (self.higher_minutes, self.decision_minutes):
            for zone in self.detectors[timeframe].active_zones(side=wanted):
                # The objective must have been observable before the causal
                # interaction, not created by confirmation of the same trade.
                if zone.observed_time_ns >= causal_cutoff_ns:
                    continue
                if side is Side.LONG and zone.lower > max(entry, current.high):
                    candidates.append((zone.lower, zone))
                elif side is Side.SHORT and zone.upper < min(entry, current.low):
                    candidates.append((zone.upper, zone))
        if not candidates:
            return None
        if side is Side.LONG:
            price, zone = min(candidates, key=lambda item: item[0])
        else:
            price, zone = max(candidates, key=lambda item: item[0])
        return zone, price

    def _trigger_formation_touched_context(self, trigger: PriceZone, setup: MTFSetup) -> bool:
        detector = self.detectors[self.trigger_minutes]
        return any(
            0 <= index < len(detector.bars)
            and self._bar_touches_zone(detector.bars[index], setup.overlap.lower, setup.overlap.upper)
            for index in trigger.formation_indices
        )

    def _consume_context(self, setup: MTFSetup) -> None:
        setup.higher_zone.consumed = True
        setup.lower_zone.consumed = True
        if setup.trigger_zone is not None:
            setup.trigger_zone.consumed = True

    def _finish_no_trade(self, setup: MTFSetup, state: SetupState, bar: Candle, reason: str, **values: Any) -> None:
        setup.state = state
        self._consume_context(setup)
        self._inc(reason)
        self._trace(reason, bar.ts_close_ns, setup, **values)

    def _make_plan(
        self,
        *,
        setup: MTFSetup,
        path: ScenarioPath,
        current: Candle,
        entry: float,
        stop: float,
        trigger_zone: PriceZone,
        interaction_time_ns: int,
        trigger_time_ns: int,
    ) -> MTFTradePlan | None:
        side = self._side_for_context(setup.overlap.side, path)
        if side is Side.LONG and not stop < entry:
            self._finish_no_trade(setup, SetupState.NO_TRADE_GEOMETRY, current, "invalid_long_geometry")
            return None
        if side is Side.SHORT and not entry < stop:
            self._finish_no_trade(setup, SetupState.NO_TRADE_GEOMETRY, current, "invalid_short_geometry")
            return None
        target_result = self._opposite_target(side, entry, current, interaction_time_ns)
        if target_result is None:
            self._finish_no_trade(
                setup,
                SetupState.TARGET_SPENT,
                current,
                "no_unspent_preexisting_target",
            )
            return None
        target_zone, target = target_result
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0.0 or reward <= 0.0:
            self._finish_no_trade(setup, SetupState.NO_TRADE_GEOMETRY, current, "nonpositive_geometry")
            return None
        gross_rr = reward / risk
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish_no_trade(
                setup,
                SetupState.NO_TRADE_GEOMETRY,
                current,
                "gross_rr_below_minimum",
                gross_rr=gross_rr,
            )
            return None

        self.sequence += 1
        family = self.REJECTION_FAMILY if path is ScenarioPath.REJECTION else self.ACCEPTANCE_FAMILY
        causal_event_id = f"{family}:{setup.setup_id}:{interaction_time_ns}:{trigger_zone.zone_id}"
        plan = MTFTradePlan(
            plan_id=f"ecv3-mtf-{self.symbol}-{self.sequence:08d}",
            causal_event_id=causal_event_id,
            symbol=self.symbol,
            family=family,
            side=side,
            observed_time_ns=current.ts_close_ns,
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
            trigger_zone_id=trigger_zone.zone_id,
            trigger_strength_ratio=trigger_zone.strength_ratio,
            target_zone_id=target_zone.zone_id,
            target_zone_kind=target_zone.kind,
            overlap_lower=setup.overlap.lower,
            overlap_upper=setup.overlap.upper,
            interaction_time_ns=interaction_time_ns,
            trigger_time_ns=trigger_time_ns,
            scenario_path=path.value,
            setup_observed_time_ns=setup.observed_time_ns,
            trigger_zone_kind=trigger_zone.kind.value,
            source_rule_count=len(SOURCE_EXPLICIT_RULES),
            rule_provenance=SOURCE_EXPLICIT_RULES + TRANSLATION_RULES,
        )
        setup.state = SetupState.PLANNED
        self._consume_context(setup)
        self.plans.append(plan)
        self._inc("plan_created")
        self._inc(f"plan_{path.value.lower()}")
        self._trace(
            "plan_created",
            current.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            family=family,
            gross_rr=gross_rr,
            entry=entry,
            stop=stop,
            target=target,
        )
        return plan

    def _decision_outside(self, setup: MTFSetup, bar: Candle) -> bool:
        if setup.overlap.side is ZoneSide.SUPPORT:
            return bar.close < setup.overlap.lower
        return bar.close > setup.overlap.upper

    def _decision_held_outside(self, setup: MTFSetup, bar: Candle) -> bool:
        if setup.overlap.side is ZoneSide.SUPPORT:
            return bar.open < setup.overlap.lower and bar.close < setup.overlap.lower
        return bar.open > setup.overlap.upper and bar.close > setup.overlap.upper

    def _advance_decision(self, bar: Candle, index: int) -> None:
        for setup in self.setups:
            if self._terminal(setup.state) or bar.ts_close_ns <= setup.observed_time_ns:
                continue
            if setup.state is SetupState.WAITING_INTERACTION and self._decision_outside(setup, bar):
                setup.state = SetupState.ACCEPTANCE_WAIT_HOLD
                setup.path = ScenarioPath.ACCEPTANCE
                setup.acceptance_break_index = index
                setup.acceptance_break_time_ns = bar.ts_close_ns
                setup.acceptance_break_extreme = bar.low if setup.overlap.side is ZoneSide.SUPPORT else bar.high
                self._inc("acceptance_break")
                self._trace("acceptance_break", bar.ts_close_ns, setup)
                continue

            if setup.state is not SetupState.ACCEPTANCE_WAIT_HOLD:
                continue
            break_index = setup.acceptance_break_index
            if break_index is None:
                raise RuntimeError("acceptance setup lost break index")
            if index != break_index + 1:
                if index > break_index + 1:
                    setup.state = SetupState.UNRESOLVED
                    self._inc("acceptance_missing_next_bar")
                    self._trace("acceptance_missing_next_bar", bar.ts_close_ns, setup)
                continue
            if self._decision_held_outside(setup, bar):
                setup.state = SetupState.ACCEPTANCE_WAIT_RETEST
                setup.confirmation_time_ns = bar.ts_close_ns
                self._inc("acceptance_hold_confirmed")
                self._trace("acceptance_hold_confirmed", bar.ts_close_ns, setup)
            else:
                # A failed hold is not silently relabeled as a good rejection.
                # The 5m path may still independently observe a reclaim episode;
                # otherwise the setup returns to unresolved interaction state.
                setup.state = SetupState.WAITING_INTERACTION
                setup.path = None
                setup.acceptance_break_index = None
                setup.acceptance_break_time_ns = None
                setup.acceptance_break_extreme = None
                self._inc("acceptance_hold_failed")
                self._trace("acceptance_hold_failed", bar.ts_close_ns, setup)

    def _arm_rejection(self, setup: MTFSetup, bar: Candle, index: int, confirmed: bool) -> None:
        setup.path = ScenarioPath.REJECTION
        setup.interaction_time_ns = bar.ts_close_ns
        setup.interaction_trigger_index = index
        setup.interaction_extreme = bar.low if setup.overlap.side is ZoneSide.SUPPORT else bar.high
        if confirmed:
            setup.state = SetupState.REJECTION_WAIT_DISPLACEMENT
            setup.confirmation_time_ns = bar.ts_close_ns
            self._inc("rejection_reclaim_confirmed")
            self._trace("rejection_reclaim_confirmed", bar.ts_close_ns, setup)
        else:
            setup.state = SetupState.REJECTION_WAIT_CONFIRM
            self._inc("rejection_excursion_unresolved")
            self._trace("rejection_excursion_unresolved", bar.ts_close_ns, setup)

    def _advance_rejection_confirmation(self, setup: MTFSetup, bar: Candle) -> None:
        if setup.interaction_extreme is None:
            raise RuntimeError("rejection setup lost interaction extreme")
        if setup.overlap.side is ZoneSide.SUPPORT:
            setup.interaction_extreme = min(setup.interaction_extreme, bar.low)
            if bar.close >= setup.overlap.upper:
                setup.state = SetupState.REJECTION_WAIT_DISPLACEMENT
                setup.confirmation_time_ns = bar.ts_close_ns
                self._inc("rejection_reclaim_confirmed")
                self._trace("rejection_reclaim_confirmed", bar.ts_close_ns, setup)
        else:
            setup.interaction_extreme = max(setup.interaction_extreme, bar.high)
            if bar.close <= setup.overlap.lower:
                setup.state = SetupState.REJECTION_WAIT_DISPLACEMENT
                setup.confirmation_time_ns = bar.ts_close_ns
                self._inc("rejection_reclaim_confirmed")
                self._trace("rejection_reclaim_confirmed", bar.ts_close_ns, setup)

    def _rejection_invalidated(self, setup: MTFSetup, bar: Candle) -> bool:
        if setup.interaction_extreme is None:
            return False
        if setup.overlap.side is ZoneSide.SUPPORT:
            return bar.low <= setup.interaction_extreme - self.tick_size
        return bar.high >= setup.interaction_extreme + self.tick_size

    def _select_event_local_trigger(self, setup: MTFSetup, created: list[PriceZone]) -> PriceZone | None:
        confirmation_time = setup.confirmation_time_ns or 0
        candidates = [
            zone
            for zone in created
            if zone.side is setup.overlap.side
            and zone.observed_time_ns > confirmation_time
            and self._trigger_formation_touched_context(zone, setup)
        ]
        if not candidates:
            return None
        # OB and FVG solve the same entry-location problem here. Prefer an
        # overlapping pair when present; otherwise use the earliest observable
        # source-valid zone, not the one with the best hindsight outcome.
        for first in candidates:
            for second in candidates:
                if first is second or first.kind is second.kind:
                    continue
                if first.overlaps(second):
                    self._inc("event_local_ob_fvg_confluence")
                    return min(
                        (first, second),
                        key=lambda zone: (zone.observed_time_ns, zone.formed_index, zone.zone_id),
                    )
        return min(candidates, key=lambda zone: (zone.observed_time_ns, zone.formed_index, zone.zone_id))

    def _advance_acceptance_retest(self, setup: MTFSetup, bar: Candle) -> MTFTradePlan | None:
        if setup.confirmation_time_ns is None or bar.ts_close_ns <= setup.confirmation_time_ns:
            return None
        if setup.overlap.side is ZoneSide.SUPPORT:
            # Broken support is retested from below and must remain resistance.
            if bar.close > setup.overlap.upper:
                self._finish_no_trade(setup, SetupState.INVALIDATED, bar, "acceptance_reentered_context")
                return None
            retested = bar.high >= setup.overlap.lower and bar.close < setup.overlap.lower
            stop = setup.overlap.upper + self.tick_size
        else:
            if bar.close < setup.overlap.lower:
                self._finish_no_trade(setup, SetupState.INVALIDATED, bar, "acceptance_reentered_context")
                return None
            retested = bar.low <= setup.overlap.upper and bar.close > setup.overlap.upper
            stop = setup.overlap.lower - self.tick_size
        if not retested:
            return None

        # The breached decision zone itself is the trigger structure for the
        # S/R flip. It already exists in the audit detector and therefore does
        # not require a synthetic, untraceable pattern object.
        trigger_zone = setup.lower_zone
        interaction_time = setup.acceptance_break_time_ns or setup.confirmation_time_ns
        return self._make_plan(
            setup=setup,
            path=ScenarioPath.ACCEPTANCE,
            current=bar,
            entry=bar.close,
            stop=stop,
            trigger_zone=trigger_zone,
            interaction_time_ns=interaction_time,
            trigger_time_ns=bar.ts_close_ns,
        )

    def _advance_trigger(self, bar: Candle, index: int, created: list[PriceZone]) -> list[MTFTradePlan]:
        plans: list[MTFTradePlan] = []
        for setup in self.setups:
            if self._terminal(setup.state) or bar.ts_close_ns <= setup.observed_time_ns:
                continue

            if setup.state is SetupState.ACCEPTANCE_WAIT_RETEST:
                plan = self._advance_acceptance_retest(setup, bar)
                if plan is not None:
                    plans.append(plan)
                continue

            if setup.state is SetupState.WAITING_INTERACTION:
                if setup.overlap.side is ZoneSide.SUPPORT and bar.low < setup.overlap.lower:
                    self._arm_rejection(setup, bar, index, confirmed=bar.close >= setup.overlap.upper)
                elif setup.overlap.side is ZoneSide.RESISTANCE and bar.high > setup.overlap.upper:
                    self._arm_rejection(setup, bar, index, confirmed=bar.close <= setup.overlap.lower)
                continue

            if setup.state is SetupState.REJECTION_WAIT_CONFIRM:
                self._advance_rejection_confirmation(setup, bar)
                continue

            if setup.state is SetupState.REJECTION_WAIT_DISPLACEMENT:
                if self._rejection_invalidated(setup, bar):
                    self._finish_no_trade(setup, SetupState.INVALIDATED, bar, "rejection_extreme_breached")
                    continue
                trigger = self._select_event_local_trigger(setup, created)
                if trigger is None:
                    continue
                setup.trigger_zone = trigger
                setup.trigger_zone_id = trigger.zone_id
                setup.trigger_time_ns = trigger.observed_time_ns
                setup.trigger_index = index
                setup.state = SetupState.REJECTION_WAIT_RETEST
                self._inc("event_local_displacement_confirmed")
                self._trace(
                    "event_local_displacement_confirmed",
                    bar.ts_close_ns,
                    setup,
                    trigger_zone_id=trigger.zone_id,
                    trigger_zone_kind=trigger.kind.value,
                )
                continue

            if setup.state is not SetupState.REJECTION_WAIT_RETEST:
                continue
            if self._rejection_invalidated(setup, bar):
                self._finish_no_trade(setup, SetupState.INVALIDATED, bar, "rejection_extreme_breached")
                continue
            trigger = setup.trigger_zone
            trigger_index = setup.trigger_index
            if trigger is None or trigger_index is None:
                raise RuntimeError("rejection retest setup lost trigger")
            if index <= trigger_index:
                continue
            if not self._bar_touches_zone(bar, trigger.lower, trigger.upper):
                continue
            if setup.overlap.side is ZoneSide.SUPPORT:
                reacted = bar.close > trigger.upper and bar.close > bar.open
                stop = (setup.interaction_extreme or bar.low) - self.tick_size
            else:
                reacted = bar.close < trigger.lower and bar.close < bar.open
                stop = (setup.interaction_extreme or bar.high) + self.tick_size
            if not reacted:
                # The first touch is the opportunity. A bar which crosses the
                # zone but cannot reject it is not silently deferred to a more
                # favorable later retest.
                self._finish_no_trade(
                    setup,
                    SetupState.UNRESOLVED,
                    bar,
                    "first_retest_failed_reaction",
                    trigger_zone_id=trigger.zone_id,
                )
                continue
            interaction_time = setup.interaction_time_ns or bar.ts_close_ns
            plan = self._make_plan(
                setup=setup,
                path=ScenarioPath.REJECTION,
                current=bar,
                entry=bar.close,
                stop=stop,
                trigger_zone=trigger,
                interaction_time_ns=interaction_time,
                trigger_time_ns=bar.ts_close_ns,
            )
            if plan is not None:
                plans.append(plan)
        return plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        if timeframe_minutes not in self.detectors:
            raise ValueError(f"unsupported timeframe: {timeframe_minutes}")
        detector = self.detectors[timeframe_minutes]
        created = detector.on_bar(bar)
        if timeframe_minutes in (self.higher_minutes, self.decision_minutes):
            self._refresh_setups(bar.ts_close_ns)
            if timeframe_minutes == self.decision_minutes:
                self._advance_decision(bar, len(detector.bars) - 1)
            return []
        return self._advance_trigger(bar, len(detector.bars) - 1, created)
