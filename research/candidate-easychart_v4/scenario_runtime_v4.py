"""Causal runtime binding for the EasyChart v4 structural scenario.

A trigger OB/FVG can finish on the same closed lower-timeframe bar which first
makes a known structural interaction observable. The footprint is armed on
that bar, but entry remains impossible until a later first retest. This keeps
the event-local evidence without introducing same-bar hindsight.
"""
from __future__ import annotations

from typing import Iterable

from domain import Candle
from easychart_mtf_scenario import MTFTradePlan
from easychart_zones import PriceZone
from scenario_bundle_v4 import (
    ResearchScenarioBundleV4 as _BaseResearchScenarioBundleV4,
    StructuralScenarioEngine,
    StructuralSetupState,
    _EvidenceDetectorView,
)


class CausalStructuralScenarioEngine(StructuralScenarioEngine):
    def _advance(
        self,
        bar: Candle,
        index: int,
        created: Iterable[PriceZone],
    ) -> list[MTFTradePlan]:
        created_zones = tuple(created)
        output: list[MTFTradePlan] = []
        for setup in list(self._active.values()):
            if (
                setup.state is StructuralSetupState.WAITING_DISPLACEMENT
                and bar.ts_close_ns >= setup.event.interaction_time_ns
            ):
                self._arm_displacement(setup, bar, index, created_zones)
            # Same-bar evidence may arm a setup, never enter it.
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
            touched = [
                zone
                for zone in live
                if bar.low <= zone.upper and bar.high >= zone.lower
            ]
            if not touched:
                continue
            trigger = min(touched, key=lambda zone: (zone.observed_time_ns, zone.zone_id))
            reacted = (
                bar.close > trigger.upper and bar.close > bar.open
                if setup.event.side.value > 0
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


class ResearchScenarioBundleV4(_BaseResearchScenarioBundleV4):
    """The same policy at macro and micro scales, with causal same-bar arming."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.macro = CausalStructuralScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = CausalStructuralScenarioEngine(
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
        self._claimed_episodes = set()
        self._bundle_trace = []


__all__ = ["CausalStructuralScenarioEngine", "ResearchScenarioBundleV4"]
