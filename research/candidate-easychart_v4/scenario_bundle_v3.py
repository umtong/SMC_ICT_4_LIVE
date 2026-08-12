"""Fast, source-separated EasyChart v3 scenario bundle.

This module leaves the audited v2/v3 Nautilus infrastructure intact and swaps
only the decision engines.  OB/FVG overlap scenarios run at 60/15/5 and
15/5/1.  Independent horizontal-liquidity Fakeout/Trap scenarios run at 60/5
and 15/1.  All families emit the same immutable pre-entry plan contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from domain import Candle, Side
from easychart_mtf_scenario import (
    MTFTradePlan,
    SOURCE_EXPLICIT_RULES,
    TRANSLATION_RULES,
    ScaleScenarioEngine,
    TargetZone,
)
from easychart_zones import EasyChartZoneDetector, PriceZone, ZoneKind, ZoneSide
from liquidity import CausalLiquidityDetector, ObjectiveKind, ObjectiveZone


class ActiveEasyChartZoneDetector(EasyChartZoneDetector):
    """Preserve the full audit archive but update only currently live zones."""

    def __init__(self, symbol: str, timeframe_minutes: int, tick_size: float) -> None:
        super().__init__(symbol, timeframe_minutes, tick_size)
        self._live: dict[str, PriceZone] = {}
        self._registered = 0

    def _sync_tail(self) -> None:
        if self._registered < len(self.zones):
            for zone in self.zones[self._registered :]:
                if zone.active:
                    self._live[zone.zone_id] = zone
            self._registered = len(self.zones)

    def _update_lifecycle(self, current: Candle, index: int) -> None:
        self._sync_tail()
        for zone_id, zone in list(self._live.items()):
            if not zone.active:
                self._live.pop(zone_id, None)
                continue
            if index <= zone.formed_index:
                continue
            if zone.side is ZoneSide.SUPPORT:
                invalidated = current.low <= zone.invalidation
                touched = current.low <= zone.upper and current.high >= zone.lower
            else:
                invalidated = current.high >= zone.invalidation
                touched = current.high >= zone.lower and current.low <= zone.upper
            if invalidated:
                zone.invalidated_index = index
                zone.invalidated_time_ns = current.ts_close_ns
                self._live.pop(zone_id, None)
                self._inc(f"{zone.kind.value.lower()}_invalidated_before_or_on_touch")
            elif touched and zone.first_touch_index is None:
                zone.first_touch_index = index
                zone.first_touch_time_ns = current.ts_close_ns
                self._inc(f"{zone.kind.value.lower()}_first_touch")

    def on_bar(self, bar: Candle) -> list[PriceZone]:
        created = super().on_bar(bar)
        for zone in created:
            self._live[zone.zone_id] = zone
        self._registered = len(self.zones)
        return created

    def active_zones(
        self,
        *,
        side: ZoneSide | None = None,
        kind: ZoneKind | None = None,
        high_quality_only: bool = False,
    ) -> list[PriceZone]:
        self._sync_tail()
        output: list[PriceZone] = []
        for zone_id, zone in list(self._live.items()):
            if not zone.active:
                self._live.pop(zone_id, None)
                continue
            if side is not None and zone.side is not side:
                continue
            if kind is not None and zone.kind is not kind:
                continue
            if high_quality_only and not zone.high_quality_by_size:
                continue
            output.append(zone)
        return output


class FastScaleScenarioEngine(ScaleScenarioEngine):
    """Existing causal overlap policy with live-zone and live-setup hot paths."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.detectors = {
            self.higher_minutes: ActiveEasyChartZoneDetector(
                self.symbol,
                self.higher_minutes,
                self.tick_size,
            ),
            self.decision_minutes: ActiveEasyChartZoneDetector(
                self.symbol,
                self.decision_minutes,
                self.tick_size,
            ),
            self.trigger_minutes: ActiveEasyChartZoneDetector(
                self.symbol,
                self.trigger_minutes,
                self.tick_size,
            ),
        }
        self.archived_setups: list[Any] = []

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        plans = super().on_bar(timeframe_minutes, bar)
        terminal = [setup for setup in self.setups if self._terminal(setup.state)]
        if terminal:
            self.archived_setups.extend(terminal)
            self.setups = [setup for setup in self.setups if not self._terminal(setup.state)]
        return plans

    @property
    def all_setups(self) -> list[Any]:
        return self.archived_setups + self.setups


class HorizontalState(str, Enum):
    WAITING_SWEEP = "WAITING_SWEEP"
    WAITING_RECLAIM = "WAITING_RECLAIM"
    WAITING_DISPLACEMENT = "WAITING_DISPLACEMENT"
    WAITING_RETEST = "WAITING_RETEST"
    PLANNED = "PLANNED"
    INVALIDATED = "INVALIDATED"
    NO_TARGET = "NO_TARGET"
    NO_TRADE_GEOMETRY = "NO_TRADE_GEOMETRY"
    UNRESOLVED = "UNRESOLVED"
    DUPLICATE_EPISODE = "DUPLICATE_EPISODE"


@dataclass(slots=True)
class HorizontalSetup:
    setup_id: str
    level: ObjectiveZone
    observed_time_ns: int
    state: HorizontalState = HorizontalState.WAITING_SWEEP
    sweep_time_ns: int | None = None
    sweep_index: int | None = None
    sweep_extreme: float | None = None
    reclaim_time_ns: int | None = None
    trigger_zone: PriceZone | None = None
    trigger_index: int | None = None


class HorizontalSweepScenarioEngine:
    """Confirmed swing level -> sweep -> reclaim -> displacement -> retest."""

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
        self.symbol = symbol
        self.tick_size = tick_size
        self.scale_name = scale_name
        self.context_minutes = context_minutes
        self.trigger_minutes = trigger_minutes
        self.minimum_gross_rr = minimum_gross_rr
        self.level_detector = CausalLiquidityDetector(
            symbol,
            context_minutes,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.trigger_detector = ActiveEasyChartZoneDetector(symbol, trigger_minutes, tick_size)
        self.setups: list[HorizontalSetup] = []
        self._active: dict[str, HorizontalSetup] = {}
        self.plans: list[MTFTradePlan] = []
        self.diagnostics: dict[str, int] = {}
        self.trace_events: list[dict[str, Any]] = []
        self.sequence = 0

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _trace(self, kind: str, time_ns: int, setup: HorizontalSetup | None = None, **values: Any) -> None:
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
                    "scenario_path": "REJECTION",
                    "overlap_lower": setup.level.lower,
                    "overlap_upper": setup.level.upper,
                    "higher_zone_id": setup.level.zone_id,
                    "decision_zone_id": setup.level.zone_id,
                },
            )
        self.trace_events.append(event)

    def drain_trace(self) -> list[dict[str, Any]]:
        output, self.trace_events = self.trace_events, []
        return output

    def _create_setups(self, levels: Iterable[ObjectiveZone]) -> None:
        for level in levels:
            setup_id = f"{self.scale_name}:HORIZONTAL:{level.zone_id}"
            setup = HorizontalSetup(setup_id, level, level.observed_time_ns)
            self.setups.append(setup)
            self._active[setup_id] = setup
            self._inc(f"level_{level.kind.value.lower()}_created")
            self._trace("horizontal_level_created", level.observed_time_ns, setup)

    def _finish(
        self,
        setup: HorizontalSetup,
        state: HorizontalState,
        bar: Candle,
        reason: str,
        **values: Any,
    ) -> None:
        setup.state = state
        self._active.pop(setup.setup_id, None)
        if setup.trigger_zone is not None:
            setup.trigger_zone.consumed = True
        self._inc(reason)
        self._trace(reason, bar.ts_close_ns, setup, **values)

    @staticmethod
    def _trade_side(level: ObjectiveZone) -> Side:
        return Side.LONG if level.side is ZoneSide.SUPPORT else Side.SHORT

    def _new_sweeps(self, bar: Candle, index: int) -> None:
        candidates: dict[ZoneSide, list[HorizontalSetup]] = {}
        for setup in list(self._active.values()):
            if setup.state is not HorizontalState.WAITING_SWEEP:
                continue
            if bar.ts_close_ns <= setup.observed_time_ns:
                continue
            level = setup.level
            if level.consumed_time_ns is not None and level.consumed_time_ns < bar.ts_close_ns:
                self._finish(
                    setup,
                    HorizontalState.INVALIDATED,
                    bar,
                    "horizontal_level_spent_before_sweep",
                )
                continue
            swept = bar.low < level.upper if level.side is ZoneSide.SUPPORT else bar.high > level.lower
            if swept:
                candidates.setdefault(level.side, []).append(setup)

        for side, group in candidates.items():
            if side is ZoneSide.SUPPORT:
                selected = max(group, key=lambda item: (item.level.upper, item.level.pivot_span))
            else:
                selected = min(group, key=lambda item: (item.level.lower, -item.level.pivot_span))
            for duplicate in group:
                if duplicate is selected:
                    continue
                self._finish(
                    duplicate,
                    HorizontalState.DUPLICATE_EPISODE,
                    bar,
                    "horizontal_levels_collapsed",
                    selected_setup_id=selected.setup_id,
                )
            selected.sweep_time_ns = bar.ts_close_ns
            selected.sweep_index = index
            selected.sweep_extreme = bar.low if side is ZoneSide.SUPPORT else bar.high
            reclaimed = (
                bar.close > selected.level.upper
                if side is ZoneSide.SUPPORT
                else bar.close < selected.level.lower
            )
            if reclaimed:
                selected.reclaim_time_ns = bar.ts_close_ns
                selected.state = HorizontalState.WAITING_DISPLACEMENT
                self._inc("horizontal_reclaim_confirmed")
                self._trace("horizontal_reclaim_confirmed", bar.ts_close_ns, selected)
            else:
                selected.state = HorizontalState.WAITING_RECLAIM
                self._inc("horizontal_sweep_unresolved")
                self._trace("horizontal_sweep_unresolved", bar.ts_close_ns, selected)

    def _reclaims(self, bar: Candle) -> None:
        for setup in list(self._active.values()):
            if setup.state is not HorizontalState.WAITING_RECLAIM:
                continue
            if setup.sweep_extreme is None:
                raise RuntimeError("horizontal setup lost sweep extreme")
            if setup.level.side is ZoneSide.SUPPORT:
                setup.sweep_extreme = min(setup.sweep_extreme, bar.low)
                reclaimed = bar.close > setup.level.upper
            else:
                setup.sweep_extreme = max(setup.sweep_extreme, bar.high)
                reclaimed = bar.close < setup.level.lower
            if reclaimed:
                setup.reclaim_time_ns = bar.ts_close_ns
                setup.state = HorizontalState.WAITING_DISPLACEMENT
                self._inc("horizontal_reclaim_confirmed")
                self._trace("horizontal_reclaim_confirmed", bar.ts_close_ns, setup)

    def _formation_touched_event(self, zone: PriceZone, setup: HorizontalSetup) -> bool:
        return any(
            0 <= index < len(self.trigger_detector.bars)
            and (
                self.trigger_detector.bars[index].low <= setup.level.upper
                if setup.level.side is ZoneSide.SUPPORT
                else self.trigger_detector.bars[index].high >= setup.level.lower
            )
            for index in zone.formation_indices
        )

    def _displacements(self, bar: Candle, index: int, created: Iterable[PriceZone]) -> None:
        for setup in list(self._active.values()):
            if setup.state is not HorizontalState.WAITING_DISPLACEMENT:
                continue
            if setup.reclaim_time_ns is None or bar.ts_close_ns <= setup.reclaim_time_ns:
                continue
            if setup.sweep_extreme is None:
                raise RuntimeError("horizontal setup lost sweep extreme")
            breached = (
                bar.low <= setup.sweep_extreme - self.tick_size
                if setup.level.side is ZoneSide.SUPPORT
                else bar.high >= setup.sweep_extreme + self.tick_size
            )
            if breached:
                self._finish(setup, HorizontalState.INVALIDATED, bar, "horizontal_extreme_breached")
                continue
            candidates = [
                zone
                for zone in created
                if zone.side is setup.level.side
                and zone.high_quality_by_size
                and zone.observed_time_ns > setup.reclaim_time_ns
                and self._formation_touched_event(zone, setup)
            ]
            if candidates:
                trigger = min(candidates, key=lambda zone: (zone.observed_time_ns, zone.zone_id))
                setup.trigger_zone = trigger
                setup.trigger_index = index
                setup.state = HorizontalState.WAITING_RETEST
                self._inc("horizontal_displacement_confirmed")
                self._trace(
                    "horizontal_displacement_confirmed",
                    bar.ts_close_ns,
                    setup,
                    trigger_zone_id=trigger.zone_id,
                    trigger_zone_kind=trigger.kind.value,
                    trigger_strength_ratio=trigger.strength_ratio,
                )

    def _target(
        self,
        setup: HorizontalSetup,
        bar: Candle,
        entry: float,
    ) -> tuple[ObjectiveZone, float] | None:
        if setup.sweep_time_ns is None:
            return None
        side = self._trade_side(setup.level)
        wanted = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        candidates: list[tuple[float, ObjectiveZone]] = []
        for zone in self.level_detector.zones:
            if zone.side is not wanted or zone.observed_time_ns >= setup.sweep_time_ns:
                continue
            if zone.consumed_time_ns is not None and zone.consumed_time_ns < setup.sweep_time_ns:
                continue
            price = zone.lower if side is Side.LONG else zone.upper
            if side is Side.LONG and price > max(entry, bar.high):
                candidates.append((price, zone))
            elif side is Side.SHORT and price < min(entry, bar.low):
                candidates.append((price, zone))
        if not candidates:
            return None
        if side is Side.LONG:
            price, zone = min(candidates, key=lambda item: (item[0], -item[1].pivot_span))
        else:
            price, zone = max(candidates, key=lambda item: (item[0], item[1].pivot_span))
        return zone, price

    def _plan(self, setup: HorizontalSetup, bar: Candle, stop: float) -> MTFTradePlan | None:
        if setup.trigger_zone is None or setup.sweep_time_ns is None:
            raise RuntimeError("horizontal plan attempted from incomplete state")
        side = self._trade_side(setup.level)
        entry = bar.close
        if (side is Side.LONG and stop >= entry) or (side is Side.SHORT and stop <= entry):
            self._finish(setup, HorizontalState.NO_TRADE_GEOMETRY, bar, "horizontal_invalid_geometry")
            return None
        target_result = self._target(setup, bar, entry)
        if target_result is None:
            self._finish(setup, HorizontalState.NO_TARGET, bar, "horizontal_no_preexisting_target")
            return None
        target_zone, target = target_result
        gross_rr = abs(target - entry) / abs(entry - stop)
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                HorizontalState.NO_TRADE_GEOMETRY,
                bar,
                "horizontal_gross_rr_below_minimum",
                gross_rr=gross_rr,
            )
            return None
        self.sequence += 1
        family = f"{self.scale_name}_HORIZONTAL_SWEEP_RECLAIM_DISPLACEMENT_RETEST"
        trigger = setup.trigger_zone
        plan = MTFTradePlan(
            plan_id=f"ecv3-{self.scale_name.lower()}-{self.symbol}-{self.sequence:08d}",
            causal_event_id=f"{family}:{setup.level.zone_id}:{setup.sweep_time_ns}:{trigger.zone_id}",
            symbol=self.symbol,
            family=family,
            side=side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=setup.level.zone_id,
            higher_zone_kind=setup.level.kind,
            higher_strength_ratio=setup.level.strength_ratio,
            lower_zone_id=setup.level.zone_id,
            lower_zone_kind=setup.level.kind,
            lower_strength_ratio=setup.level.strength_ratio,
            trigger_zone_id=trigger.zone_id,
            trigger_strength_ratio=trigger.strength_ratio,
            target_zone_id=target_zone.zone_id,
            target_zone_kind=target_zone.kind,
            overlap_lower=setup.level.lower,
            overlap_upper=setup.level.upper,
            interaction_time_ns=setup.sweep_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path="REJECTION",
            setup_observed_time_ns=setup.observed_time_ns,
            trigger_zone_kind=trigger.kind.value,
            source_rule_count=len(SOURCE_EXPLICIT_RULES),
            rule_provenance=SOURCE_EXPLICIT_RULES
            + TRANSLATION_RULES
            + (
                "SOURCE_EXPLICIT:FAKEOUT_OCCURS_AT_PRIOR_SWING_HIGH_LOW",
                "RESEARCH_HYPOTHESIS:CONFIRMED_PIVOT_IS_MACHINE_HORIZONTAL_LEVEL",
            ),
            scale_name=self.scale_name,
            higher_timeframe_minutes=self.context_minutes,
            decision_timeframe_minutes=self.context_minutes,
            trigger_timeframe_minutes=self.trigger_minutes,
        )
        setup.state = HorizontalState.PLANNED
        self._active.pop(setup.setup_id, None)
        trigger.consumed = True
        self.plans.append(plan)
        self._inc("horizontal_plan_created")
        self._trace(
            "horizontal_plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
        )
        return plan

    def _retests(self, bar: Candle, index: int) -> list[MTFTradePlan]:
        output: list[MTFTradePlan] = []
        for setup in list(self._active.values()):
            if setup.state is not HorizontalState.WAITING_RETEST:
                continue
            trigger = setup.trigger_zone
            if trigger is None or setup.trigger_index is None or setup.sweep_extreme is None:
                raise RuntimeError("horizontal retest setup lost state")
            breached = (
                bar.low <= setup.sweep_extreme - self.tick_size
                if setup.level.side is ZoneSide.SUPPORT
                else bar.high >= setup.sweep_extreme + self.tick_size
            )
            if breached:
                self._finish(setup, HorizontalState.INVALIDATED, bar, "horizontal_extreme_breached")
                continue
            touched = bar.low <= trigger.upper and bar.high >= trigger.lower
            if index <= setup.trigger_index or not touched:
                continue
            if setup.level.side is ZoneSide.SUPPORT:
                reacted = bar.close > trigger.upper and bar.close > bar.open
                stop = min(setup.sweep_extreme - self.tick_size, trigger.invalidation)
            else:
                reacted = bar.close < trigger.lower and bar.close < bar.open
                stop = max(setup.sweep_extreme + self.tick_size, trigger.invalidation)
            if not reacted:
                self._finish(setup, HorizontalState.UNRESOLVED, bar, "horizontal_first_retest_failed")
                continue
            plan = self._plan(setup, bar, stop)
            if plan is not None:
                output.append(plan)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        if timeframe_minutes == self.context_minutes:
            self._create_setups(self.level_detector.on_bar(bar))
            return []
        if timeframe_minutes != self.trigger_minutes:
            raise ValueError(f"unsupported horizontal timeframe {timeframe_minutes}")
        self.level_detector.observe_price(bar)
        created = self.trigger_detector.on_bar(bar)
        index = len(self.trigger_detector.bars) - 1
        self._reclaims(bar)
        self._new_sweeps(bar, index)
        self._displacements(bar, index, created)
        return self._retests(bar, index)

    def find_zone(self, zone_id: str) -> ObjectiveZone | PriceZone | None:
        for zone in self.level_detector.zones:
            if zone.zone_id == zone_id:
                return zone
        for zone in self.trigger_detector.zones:
            if zone.zone_id == zone_id:
                return zone
        return None


class ResearchScenarioBundle:
    """Four independent EasyChart families, one causal plan stream per symbol."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.macro = FastScaleScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO",
            higher_minutes=60,
            decision_minutes=15,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = FastScaleScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal_macro = HorizontalSweepScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO_HORIZONTAL",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal_micro = HorizontalSweepScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO_HORIZONTAL",
            context_minutes=15,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = {
            60: self.macro.detectors[60],
            15: self.macro.detectors[15],
            5: self.macro.detectors[5],
        }
        self._claimed_episodes: set[tuple[Side, int]] = set()
        self._bundle_trace: list[dict[str, Any]] = []

    @property
    def setups(self) -> list[Any]:
        return (
            self.macro.all_setups
            + self.micro.all_setups
            + self.horizontal_macro.setups
            + self.horizontal_micro.setups
        )

    @property
    def plans(self) -> list[MTFTradePlan]:
        return (
            self.macro.plans
            + self.micro.plans
            + self.horizontal_macro.plans
            + self.horizontal_micro.plans
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "macro": self.macro.diagnostics,
            "micro": self.micro.diagnostics,
            "horizontal_macro": self.horizontal_macro.diagnostics,
            "horizontal_micro": self.horizontal_micro.diagnostics,
        }

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        plans: list[MTFTradePlan] = []
        if timeframe_minutes in self.macro.detectors:
            plans.extend(self.macro.on_bar(timeframe_minutes, bar))
        if timeframe_minutes in self.micro.detectors:
            plans.extend(self.micro.on_bar(timeframe_minutes, bar))
        if timeframe_minutes in (60, 5):
            plans.extend(self.horizontal_macro.on_bar(timeframe_minutes, bar))
        if timeframe_minutes in (15, 1):
            plans.extend(self.horizontal_micro.on_bar(timeframe_minutes, bar))
        ranked = sorted(
            plans,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                -len({plan.higher_zone_kind, plan.lower_zone_kind}),
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
        output = (
            self.macro.drain_trace()
            + self.micro.drain_trace()
            + self.horizontal_macro.drain_trace()
            + self.horizontal_micro.drain_trace()
            + self._bundle_trace
        )
        self._bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> TargetZone | None:
        return (
            self.macro.find_zone(zone_id)
            or self.micro.find_zone(zone_id)
            or self.horizontal_macro.find_zone(zone_id)
            or self.horizontal_micro.find_zone(zone_id)
        )
