"""Causal runtime binding for the EasyChart v4 structural scenario.

A trigger OB/FVG can finish on the same closed lower-timeframe bar which first
makes a known structural interaction observable. The footprint is armed on
that bar, but entry remains impossible until a later first retest.

A context-timeframe Fakeout is only an interaction candidate. EasyChart's
material describes a strong, immediate opposite move after the sweep, so the
next context candle must close beyond the Fakeout candle's opposite extreme
before lower-timeframe displacement is allowed to arm a trade.
"""
from __future__ import annotations

from typing import Iterable

from domain import Candle, Side
from easychart_mtf_scenario import MTFTradePlan
from easychart_zones import PriceZone
from market_structure import StructurePath
from scenario_bundle_v4 import (
    ResearchScenarioBundleV4 as _BaseResearchScenarioBundleV4,
    StructuralScenarioEngine,
    StructuralSetupState,
    _EvidenceDetectorView,
)


class CausalStructuralScenarioEngine(StructuralScenarioEngine):
    SOURCE_RULES = StructuralScenarioEngine.SOURCE_RULES + (
        "SOURCE_EXPLICIT:FAKEOUT_NEEDS_IMMEDIATE_OPPOSITE_REVERSAL",
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pending_fakeout_confirmation: dict[str, float] = {}

    def _create_setups(self, events) -> None:
        events = tuple(events)
        start = len(self.setups)
        super()._create_setups(events)
        for setup in self.setups[start:]:
            if (
                setup.event.path is not StructurePath.FAKEOUT
                or setup.state is not StructuralSetupState.WAITING_DISPLACEMENT
            ):
                continue
            interaction = self.structure.bars[setup.event.interaction_index]
            level = interaction.high if setup.event.side is Side.LONG else interaction.low
            self._pending_fakeout_confirmation[setup.setup_id] = level
            self._inc("fakeout_reversal_confirmation_required")
            self._trace(
                "fakeout_reversal_confirmation_required",
                setup.event.interaction_time_ns,
                setup,
                reversal_confirmation_price=level,
            )

    def _resolve_fakeout_confirmations(self, bar: Candle) -> None:
        for setup_id, level in list(self._pending_fakeout_confirmation.items()):
            setup = self._active.get(setup_id)
            if setup is None:
                self._pending_fakeout_confirmation.pop(setup_id, None)
                continue
            if bar.ts_close_ns <= setup.event.interaction_time_ns:
                continue
            self._pending_fakeout_confirmation.pop(setup_id, None)
            if self._invalidated_before_entry(setup, bar):
                self._finish(
                    setup,
                    StructuralSetupState.INVALIDATED,
                    bar,
                    "fakeout_extreme_breached_before_confirmation",
                )
                continue
            if self._target_spent_before_entry(setup, bar):
                self._finish(
                    setup,
                    StructuralSetupState.TARGET_SPENT,
                    bar,
                    "fakeout_target_spent_during_confirmation",
                )
                continue
            confirmed = (
                bar.close > level
                if setup.event.side is Side.LONG
                else bar.close < level
            )
            if confirmed:
                self._inc("fakeout_context_reversal_confirmed")
                self._trace(
                    "fakeout_context_reversal_confirmed",
                    bar.ts_close_ns,
                    setup,
                    reversal_confirmation_price=level,
                    confirmation_close=bar.close,
                )
            else:
                self._finish(
                    setup,
                    StructuralSetupState.INVALIDATED,
                    bar,
                    "fakeout_next_context_bar_failed_reversal",
                    reversal_confirmation_price=level,
                    confirmation_close=bar.close,
                )

    def _advance(
        self,
        bar: Candle,
        index: int,
        created: Iterable[PriceZone],
    ) -> list[MTFTradePlan]:
        created_zones = tuple(created)
        output: list[MTFTradePlan] = []
        for setup in list(self._active.values()):
            if setup.setup_id in self._pending_fakeout_confirmation:
                # The event remains invalidated immediately if its sweep extreme
                # or objective is crossed before the next context close.
                if bar.ts_close_ns > setup.event.interaction_time_ns:
                    if self._invalidated_before_entry(setup, bar):
                        self._pending_fakeout_confirmation.pop(setup.setup_id, None)
                        self._finish(
                            setup,
                            StructuralSetupState.INVALIDATED,
                            bar,
                            "fakeout_extreme_breached_before_confirmation",
                        )
                    elif self._target_spent_before_entry(setup, bar):
                        self._pending_fakeout_confirmation.pop(setup.setup_id, None)
                        self._finish(
                            setup,
                            StructuralSetupState.TARGET_SPENT,
                            bar,
                            "fakeout_target_spent_during_confirmation",
                        )
                continue
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
            self._resolve_fakeout_confirmations(bar)
        return super().on_bar(timeframe_minutes, bar)


class ResearchScenarioBundleV4(_BaseResearchScenarioBundleV4):
    """The same policy at macro and micro scales, with causal confirmations."""

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
