"""EasyChart v3 planned-zone limit-entry policy.

The source material repeatedly shows a complete scenario being recognized first,
then a buy/sell order being parked at the OB/FVG zone.  A later bar touching the
zone is execution, not new alpha confirmation.  These engines therefore emit a
plan as soon as a source-sized event-local trigger becomes observable and set one
full GTC limit order at the trigger's proximal edge.
"""
from __future__ import annotations

from typing import Any, Iterable

from domain import Candle
from easychart_mtf_scenario import MTFTradePlan, SetupState
from easychart_zones import PriceZone, ZoneSide
from horizontal_structure_v3 import (
    StrongHorizontalSweepScenarioEngine,
    StrongResearchScenarioBundle,
)
from scenario_bundle_v3 import FastScaleScenarioEngine, HorizontalState


class LimitScaleScenarioEngine(FastScaleScenarioEngine):
    """Cross-timeframe context with one predeclared limit at the trigger zone."""

    def _plan_displacements(
        self,
        bar: Candle,
        index: int,
        created: Iterable[PriceZone],
    ) -> list[MTFTradePlan]:
        plans: list[MTFTradePlan] = []
        for setup in self.setups:
            if setup.state is not SetupState.WAITING_DISPLACEMENT:
                continue
            if self._sweep_extreme_breached(setup, bar):
                self._finish(setup, SetupState.INVALIDATED, bar, "rejection_extreme_breached")
                continue
            if setup.path is not None and setup.path.value == "TOUCH":
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
            if setup.overlap.side is ZoneSide.SUPPORT:
                entry = trigger.upper
                stop = min(
                    (setup.interaction_extreme or setup.overlap.lower) - self.tick_size,
                    trigger.invalidation,
                )
                is_future_retest = entry < bar.close
            else:
                entry = trigger.lower
                stop = max(
                    (setup.interaction_extreme or setup.overlap.upper) + self.tick_size,
                    trigger.invalidation,
                )
                is_future_retest = entry > bar.close
            if not is_future_retest:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar,
                    "trigger_zone_not_a_future_limit_retest",
                    trigger_zone_id=trigger.zone_id,
                    planned_entry=entry,
                    current_close=bar.close,
                )
                continue
            plan = self._make_plan(setup, bar, entry, stop)
            if plan is not None:
                plans.append(plan)
                self._inc("planned_limit_created_at_displacement")
                self._trace(
                    "planned_limit_created_at_displacement",
                    bar.ts_close_ns,
                    setup,
                    plan_id=plan.plan_id,
                    planned_entry=entry,
                    trigger_zone_id=trigger.zone_id,
                )
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
        plans = self._plan_displacements(bar, index, created)
        terminal = [setup for setup in self.setups if self._terminal(setup.state)]
        if terminal:
            self.archived_setups.extend(terminal)
            self.setups = [setup for setup in self.setups if not self._terminal(setup.state)]
        return plans


class LimitStrongHorizontalSweepScenarioEngine(StrongHorizontalSweepScenarioEngine):
    """Repeated-defense Fakeout/Trap with a parked trigger-zone limit."""

    def _plan_displacements(
        self,
        bar: Candle,
        index: int,
        created: Iterable[PriceZone],
    ) -> list[MTFTradePlan]:
        plans: list[MTFTradePlan] = []
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
            if not candidates:
                continue
            trigger = min(candidates, key=lambda zone: (zone.observed_time_ns, zone.zone_id))
            setup.trigger_zone = trigger
            setup.trigger_index = index
            if setup.level.side is ZoneSide.SUPPORT:
                entry = trigger.upper
                stop = min(setup.sweep_extreme - self.tick_size, trigger.invalidation)
                is_future_retest = entry < bar.close
            else:
                entry = trigger.lower
                stop = max(setup.sweep_extreme + self.tick_size, trigger.invalidation)
                is_future_retest = entry > bar.close
            if not is_future_retest:
                self._finish(
                    setup,
                    HorizontalState.UNRESOLVED,
                    bar,
                    "horizontal_trigger_not_a_future_limit_retest",
                    trigger_zone_id=trigger.zone_id,
                    planned_entry=entry,
                    current_close=bar.close,
                )
                continue
            planned_bar = Candle(
                ts_close_ns=bar.ts_close_ns,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=entry,
                volume=bar.volume,
            )
            plan = self._plan(setup, planned_bar, stop)
            if plan is not None:
                plans.append(plan)
                self._inc("horizontal_planned_limit_created_at_displacement")
                self._trace(
                    "horizontal_planned_limit_created_at_displacement",
                    bar.ts_close_ns,
                    setup,
                    plan_id=plan.plan_id,
                    planned_entry=entry,
                    trigger_zone_id=trigger.zone_id,
                )
        return plans

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
        return self._plan_displacements(bar, index, created)


class LimitResearchScenarioBundle(StrongResearchScenarioBundle):
    """All v3 source families using one planned-zone limit execution policy."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.macro = LimitScaleScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO_LIMIT",
            higher_minutes=60,
            decision_minutes=15,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = LimitScaleScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO_LIMIT",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal_macro = LimitStrongHorizontalSweepScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO_HORIZONTAL_STRUCTURE_LIMIT",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal_micro = LimitStrongHorizontalSweepScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO_HORIZONTAL_STRUCTURE_LIMIT",
            context_minutes=15,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = {
            60: self.macro.detectors[60],
            15: self.macro.detectors[15],
            5: self.macro.detectors[5],
        }
