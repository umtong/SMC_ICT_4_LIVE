"""Unified EasyChart v4 structure -> footprint -> execution scenario.

OB/FVG, trendlines, channels and Fakeout/Trap are not independent strategies.
One market scene supplies roles in this order:

    confirmed structure/liquidity
    -> boundary interaction or accepted break
    -> event-local OB/FVG displacement
    -> first later retest with reaction
    -> structural invalidation and structural objective

The two scale instances below are the same policy at 60m/5m and 15m/1m.
They widen the opportunity set without changing the decision grammar.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from domain import Candle, Side
from easychart_mtf_scenario import MTFTradePlan
from easychart_zones import PriceZone, ZoneSide
from market_structure import (
    MarketStructureDetector,
    StructuralBoundary,
    StructureEvent,
    StructurePath,
)
from scenario_bundle_v3 import ActiveEasyChartZoneDetector


class StructuralSetupState(str, Enum):
    WAITING_DISPLACEMENT = "WAITING_DISPLACEMENT"
    WAITING_RETEST = "WAITING_RETEST"
    PLANNED = "PLANNED"
    INVALIDATED = "INVALIDATED"
    TARGET_SPENT = "TARGET_SPENT"
    NO_TARGET = "NO_TARGET"
    NO_TRADE_GEOMETRY = "NO_TRADE_GEOMETRY"
    FIRST_RETEST_UNRESOLVED = "FIRST_RETEST_UNRESOLVED"
    DUPLICATE_EPISODE = "DUPLICATE_EPISODE"


@dataclass(slots=True)
class StructuralSetup:
    setup_id: str
    event: StructureEvent
    state: StructuralSetupState
    trigger_zones: tuple[PriceZone, ...] = ()
    trigger_armed_index: int | None = None
    trigger_armed_time_ns: int | None = None


class StructuralScenarioEngine:
    """One causal EasyChart policy on one context/trigger scale."""

    SOURCE_RULES = (
        "SOURCE_EXPLICIT:STRUCTURE_SUPPLIES_DIRECTION_AND_LIQUIDITY",
        "SOURCE_EXPLICIT:OB_FVG_AT_MEANINGFUL_STRUCTURE_REFINE_ENTRY",
        "SOURCE_EXPLICIT:FVG_OR_OB_DISPLACEMENT_BODY_RATIO_AT_LEAST_TWO",
        "SOURCE_EXPLICIT:FIRST_RETEST_ENTRY_AND_NO_CHASE",
        "SOURCE_EXPLICIT:STOP_BEYOND_EVENT_INVALIDATION",
        "SOURCE_EXPLICIT:TARGET_OPPOSITE_CHANNEL_OR_PREEXISTING_STRUCTURE",
    )
    TRANSLATION_RULES = (
        "HUMAN_NATURAL_INFERENCE:STRUCTURAL_EVENT_AND_FOOTPRINT_HAVE_DISTINCT_ROLES",
        "HUMAN_NATURAL_INFERENCE:SAME_DISPLACEMENT_OB_AND_FVG_ARE_ONE_TRIGGER_EPISODE",
        "HUMAN_NATURAL_INFERENCE:FIRST_RETEST_IS_CONSUMED_WHETHER_OR_NOT_IT_REACTS",
        "RESEARCH_HYPOTHESIS:MACRO_60_5_AND_MICRO_15_1_SHARE_ONE_POLICY",
        "RESEARCH_HYPOTHESIS:STRUCTURAL_STOP_OVERRIDES_TINY_TRIGGER_ZONE_STOP",
    )

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        *,
        scale_name: str,
        context_minutes: int,
        trigger_minutes: int,
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
        self.minimum_gross_rr = minimum_gross_rr
        self.structure = MarketStructureDetector(
            symbol,
            context_minutes,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.trigger_detector = ActiveEasyChartZoneDetector(symbol, trigger_minutes, tick_size)
        self.setups: list[StructuralSetup] = []
        self._active: dict[str, StructuralSetup] = {}
        self.plans: list[MTFTradePlan] = []
        self.trace_events: list[dict[str, Any]] = []
        self.diagnostics: dict[str, int] = {}
        self.sequence = 0

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _trace(
        self,
        kind: str,
        time_ns: int,
        setup: StructuralSetup | None = None,
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
            primary = self.structure.find_boundary(setup.event.primary_boundary_id)
            event.update(
                {
                    "setup_id": setup.setup_id,
                    "setup_state": setup.state.value,
                    "scenario_path": setup.event.path.value,
                    "higher_zone_id": setup.event.primary_boundary_id,
                    "decision_zone_id": (
                        setup.event.supporting_boundary_ids[0]
                        if setup.event.supporting_boundary_ids
                        else setup.event.primary_boundary_id
                    ),
                    "overlap_lower": None if primary is None else primary.level_at(time_ns) - self.tick_size,
                    "overlap_upper": None if primary is None else primary.level_at(time_ns) + self.tick_size,
                    "structure_kind": setup.event.structure_kind.value,
                    "channel_id": setup.event.channel_id,
                },
            )
        self.trace_events.append(event)

    def drain_trace(self) -> list[dict[str, Any]]:
        output, self.trace_events = self.trace_events, []
        return output

    def find_zone(self, zone_id: str) -> StructuralBoundary | PriceZone | None:
        boundary = self.structure.find_boundary(zone_id)
        if boundary is not None:
            return boundary
        for zone in self.trigger_detector.zones:
            if zone.zone_id == zone_id:
                return zone
        return None

    def _target_price(self, setup: StructuralSetup, ts_ns: int) -> float | None:
        target_id = setup.event.target_boundary_id
        if target_id is None:
            return None
        target = self.structure.find_boundary(target_id)
        return None if target is None else target.level_at(ts_ns)

    @staticmethod
    def _zone_side(side: Side) -> ZoneSide:
        return ZoneSide.SUPPORT if side is Side.LONG else ZoneSide.RESISTANCE

    def _formation_connected(self, zone: PriceZone, event: StructureEvent) -> bool:
        boundary = self.structure.find_boundary(event.primary_boundary_id)
        if boundary is None:
            return False
        for index in zone.formation_indices:
            if not 0 <= index < len(self.trigger_detector.bars):
                continue
            candle = self.trigger_detector.bars[index]
            level = boundary.level_at(candle.ts_close_ns)
            if event.side is Side.LONG and candle.low <= level + self.tick_size:
                return True
            if event.side is Side.SHORT and candle.high >= level - self.tick_size:
                return True
        return event.path in {
            StructurePath.ACCEPTANCE,
            StructurePath.CHANNEL_FAILURE_ACCEPTANCE,
        }

    def _create_setups(self, events: Iterable[StructureEvent]) -> None:
        for event in events:
            setup = StructuralSetup(
                setup_id=f"{self.scale_name}:STRUCTURE:{event.event_id}",
                event=event,
                state=(
                    StructuralSetupState.NO_TARGET
                    if event.target_boundary_id is None
                    else StructuralSetupState.WAITING_DISPLACEMENT
                ),
            )
            self.setups.append(setup)
            if setup.state is StructuralSetupState.NO_TARGET:
                self._inc("structure_event_without_predeclared_target")
                self._trace("structure_event_without_predeclared_target", event.interaction_time_ns, setup)
                continue
            self._active[setup.setup_id] = setup
            self._inc(f"setup_{event.path.value.lower()}_created")
            self._trace(
                "structural_setup_created",
                event.interaction_time_ns,
                setup,
                interaction_extreme=event.interaction_extreme,
                stop_reference=event.stop_reference,
                target_boundary_id=event.target_boundary_id,
                supporting_boundary_ids=list(event.supporting_boundary_ids),
            )

    def _finish(
        self,
        setup: StructuralSetup,
        state: StructuralSetupState,
        bar: Candle,
        reason: str,
        **values: Any,
    ) -> None:
        setup.state = state
        self._active.pop(setup.setup_id, None)
        for zone in setup.trigger_zones:
            zone.consumed = True
        self._inc(reason)
        self._trace(reason, bar.ts_close_ns, setup, **values)

    def _invalidated_before_entry(self, setup: StructuralSetup, bar: Candle) -> bool:
        stop = setup.event.stop_reference
        if setup.event.side is Side.LONG:
            return bar.low <= stop
        return bar.high >= stop

    def _target_spent_before_entry(self, setup: StructuralSetup, bar: Candle) -> bool:
        target = self._target_price(setup, bar.ts_close_ns)
        if target is None:
            return True
        if setup.event.side is Side.LONG:
            return bar.high >= target
        return bar.low <= target

    def _arm_displacement(
        self,
        setup: StructuralSetup,
        bar: Candle,
        index: int,
        created: Iterable[PriceZone],
    ) -> None:
        if bar.ts_close_ns < setup.event.interaction_time_ns:
            return
        wanted = self._zone_side(setup.event.side)
        candidates = [
            zone
            for zone in created
            if zone.side is wanted
            and zone.high_quality_by_size
            and zone.observed_time_ns >= setup.event.interaction_time_ns
            and self._formation_connected(zone, setup.event)
            and (
                zone.impulse_extreme > setup.event.reference_close
                if setup.event.side is Side.LONG
                else zone.impulse_extreme < setup.event.reference_close
            )
        ]
        if not candidates:
            return
        earliest_time = min(zone.observed_time_ns for zone in candidates)
        same_displacement = tuple(
            sorted(
                (zone for zone in candidates if zone.observed_time_ns == earliest_time),
                key=lambda zone: (zone.kind.value, zone.zone_id),
            ),
        )
        setup.trigger_zones = same_displacement
        setup.trigger_armed_index = index
        setup.trigger_armed_time_ns = bar.ts_close_ns
        setup.state = StructuralSetupState.WAITING_RETEST
        self._inc("event_local_displacement_confirmed")
        self._trace(
            "event_local_displacement_confirmed",
            bar.ts_close_ns,
            setup,
            trigger_zone_ids=[zone.zone_id for zone in same_displacement],
            trigger_zone_kinds=[zone.kind.value for zone in same_displacement],
            trigger_strength_ratios=[zone.strength_ratio for zone in same_displacement],
        )

    def _plan(
        self,
        setup: StructuralSetup,
        trigger: PriceZone,
        bar: Candle,
    ) -> MTFTradePlan | None:
        entry = bar.close
        stop = setup.event.stop_reference
        target = self._target_price(setup, bar.ts_close_ns)
        if target is None:
            self._finish(setup, StructuralSetupState.NO_TARGET, bar, "target_unavailable_at_entry")
            return None
        side = setup.event.side
        valid = stop < entry < target if side is Side.LONG else target < entry < stop
        if not valid:
            self._finish(
                setup,
                StructuralSetupState.NO_TRADE_GEOMETRY,
                bar,
                "invalid_structural_geometry",
                entry=entry,
                stop=stop,
                target=target,
            )
            return None
        gross_rr = abs(target - entry) / abs(entry - stop)
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                StructuralSetupState.NO_TRADE_GEOMETRY,
                bar,
                "gross_rr_below_minimum",
                gross_rr=gross_rr,
                entry=entry,
                stop=stop,
                target=target,
            )
            return None
        primary = self.structure.find_boundary(setup.event.primary_boundary_id)
        if primary is None:
            raise RuntimeError("structural plan lost its primary boundary")
        secondary = (
            self.structure.find_boundary(setup.event.supporting_boundary_ids[0])
            if setup.event.supporting_boundary_ids
            else primary
        )
        if secondary is None:
            secondary = primary
        target_boundary = self.structure.find_boundary(setup.event.target_boundary_id or "")
        if target_boundary is None:
            raise RuntimeError("structural plan lost its target boundary")
        self.sequence += 1
        family = f"{self.scale_name}_{setup.event.structure_kind.value}_{setup.event.path.value}_DISPLACEMENT_RETEST"
        plan = MTFTradePlan(
            plan_id=f"ecv4-{self.scale_name.lower()}-{self.symbol}-{self.sequence:08d}",
            causal_event_id=(
                f"{family}:{setup.event.primary_boundary_id}:"
                f"{setup.event.interaction_time_ns}:{trigger.zone_id}"
            ),
            symbol=self.symbol,
            family=family,
            side=side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=primary.boundary_id,
            higher_zone_kind=primary.kind,
            higher_strength_ratio=primary.strength_ratio,
            lower_zone_id=secondary.boundary_id,
            lower_zone_kind=secondary.kind,
            lower_strength_ratio=secondary.strength_ratio,
            trigger_zone_id=trigger.zone_id,
            trigger_strength_ratio=trigger.strength_ratio,
            target_zone_id=target_boundary.boundary_id,
            target_zone_kind=target_boundary.kind,
            overlap_lower=primary.level_at(setup.event.interaction_time_ns) - self.tick_size,
            overlap_upper=primary.level_at(setup.event.interaction_time_ns) + self.tick_size,
            interaction_time_ns=setup.event.interaction_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path=setup.event.path.value,
            setup_observed_time_ns=setup.event.interaction_time_ns,
            trigger_zone_kind=trigger.kind.value,
            source_rule_count=len(self.SOURCE_RULES) + len(self.structure.SOURCE_RULES),
            rule_provenance=(
                self.structure.SOURCE_RULES
                + self.SOURCE_RULES
                + self.structure.TRANSLATION_RULES
                + self.TRANSLATION_RULES
            ),
            scale_name=self.scale_name,
            higher_timeframe_minutes=self.context_minutes,
            decision_timeframe_minutes=self.context_minutes,
            trigger_timeframe_minutes=self.trigger_minutes,
        )
        setup.state = StructuralSetupState.PLANNED
        self._active.pop(setup.setup_id, None)
        for zone in setup.trigger_zones:
            zone.consumed = True
        self.plans.append(plan)
        self._inc("structural_plan_created")
        self._trace(
            "structural_plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            family=family,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            trigger_zone_id=trigger.zone_id,
        )
        return plan

    def _advance(
        self,
        bar: Candle,
        index: int,
        created: Iterable[PriceZone],
    ) -> list[MTFTradePlan]:
        output: list[MTFTradePlan] = []
        for setup in list(self._active.values()):
            if bar.ts_close_ns <= setup.event.interaction_time_ns:
                continue
            if self._invalidated_before_entry(setup, bar):
                self._finish(
                    setup,
                    StructuralSetupState.INVALIDATED,
                    bar,
                    "structural_stop_breached_before_entry",
                )
                continue
            if self._target_spent_before_entry(setup, bar):
                self._finish(
                    setup,
                    StructuralSetupState.TARGET_SPENT,
                    bar,
                    "structural_target_spent_before_entry",
                )
                continue
            if setup.state is StructuralSetupState.WAITING_DISPLACEMENT:
                self._arm_displacement(setup, bar, index, created)
                continue
            if setup.state is not StructuralSetupState.WAITING_RETEST:
                continue
            if setup.trigger_armed_index is None or index <= setup.trigger_armed_index:
                continue
            live = [zone for zone in setup.trigger_zones if zone.active]
            if not live:
                self._finish(
                    setup,
                    StructuralSetupState.INVALIDATED,
                    bar,
                    "trigger_footprint_invalidated_before_retest",
                )
                continue
            touched = [zone for zone in live if bar.low <= zone.upper and bar.high >= zone.lower]
            if not touched:
                continue
            trigger = min(touched, key=lambda zone: (zone.observed_time_ns, zone.zone_id))
            reacted = (
                bar.close > trigger.upper and bar.close > bar.open
                if setup.event.side is Side.LONG
                else bar.close < trigger.lower and bar.close < bar.open
            )
            if not reacted:
                self._finish(
                    setup,
                    StructuralSetupState.FIRST_RETEST_UNRESOLVED,
                    bar,
                    "first_retest_failed_reaction",
                    trigger_zone_id=trigger.zone_id,
                )
                continue
            plan = self._plan(setup, trigger, bar)
            if plan is not None:
                output.append(plan)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        if timeframe_minutes == self.context_minutes:
            self._create_setups(self.structure.on_bar(bar))
            return []
        if timeframe_minutes != self.trigger_minutes:
            raise ValueError(f"unsupported structural timeframe {timeframe_minutes}")
        created = self.trigger_detector.on_bar(bar)
        lower_events = self.structure.observe_lower_bar(bar)
        self._create_setups(lower_events)
        index = len(self.trigger_detector.bars) - 1
        return self._advance(bar, index, created)


class _EvidenceDetectorView(dict[int, Any]):
    """Keep v3 evidence exporters compatible without hiding 1m zone lookup."""

    def __init__(self, visible: dict[int, Any], lookup_only: tuple[Any, ...]) -> None:
        super().__init__(visible)
        self._lookup_only = lookup_only

    def values(self):  # type: ignore[override]
        return list(super().values()) + list(self._lookup_only)


class ResearchScenarioBundleV4:
    """One decision grammar at two causal scales, one plan stream per symbol."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.macro = StructuralScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = StructuralScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            context_minutes=15,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = _EvidenceDetectorView(
            {
                60: self.macro.structure,
                15: self.micro.structure,
                5: self.macro.trigger_detector,
            },
            (self.micro.trigger_detector,),
        )
        self._claimed_episodes: set[tuple[Side, int]] = set()
        self._bundle_trace: list[dict[str, Any]] = []

    @property
    def setups(self) -> list[StructuralSetup]:
        return self.macro.setups + self.micro.setups

    @property
    def plans(self) -> list[MTFTradePlan]:
        return self.macro.plans + self.micro.plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "macro": self.macro.diagnostics,
            "micro": self.micro.diagnostics,
            "macro_structure": self.macro.structure.diagnostics,
            "micro_structure": self.micro.structure.diagnostics,
        }

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        plans: list[MTFTradePlan] = []
        if timeframe_minutes in (60, 5):
            plans.extend(self.macro.on_bar(timeframe_minutes, bar))
        if timeframe_minutes in (15, 1):
            plans.extend(self.micro.on_bar(timeframe_minutes, bar))
        ranked = sorted(
            plans,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.observed_time_ns,
                plan.plan_id,
            ),
        )
        independent: list[MTFTradePlan] = []
        for plan in ranked:
            episode = (plan.side, plan.interaction_time_ns)
            if episode in self._claimed_episodes:
                self._bundle_trace.append(
                    {
                        "scenario_kind": "causal_episode_duplicate_suppressed",
                        "event_time_ns": plan.observed_time_ns,
                        "scale_name": plan.scale_name,
                        "higher_timeframe_minutes": plan.higher_timeframe_minutes,
                        "decision_timeframe_minutes": plan.decision_timeframe_minutes,
                        "trigger_timeframe_minutes": plan.trigger_timeframe_minutes,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "interaction_time_ns": plan.interaction_time_ns,
                    },
                )
                continue
            self._claimed_episodes.add(episode)
            independent.append(plan)
        return independent

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.macro.drain_trace() + self.micro.drain_trace() + self._bundle_trace
        self._bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> StructuralBoundary | PriceZone | None:
        return self.macro.find_zone(zone_id) or self.micro.find_zone(zone_id)


__all__ = [
    "ResearchScenarioBundleV4",
    "StructuralScenarioEngine",
    "StructuralSetup",
    "StructuralSetupState",
]
