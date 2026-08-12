"""Gate accepted-break entries behind the broken structure's own first retest.

The prior retest-confirmed runtime delayed *directional context*, but the
accepted-break setup itself could still arm an OB/FVG and submit a trade before
the broken boundary had ever been revisited.  That is not the EasyChart
breakout sequence.  A human trader first observes acceptance, then waits for
the S/R flip to hold, and only then uses lower-timeframe displacement to refine
an entry.

This module makes the same state transition control both routing and the trade
setup:

    close outside -> next context bar holds outside
    -> first later retest of the broken boundary closes on the new side
    -> event-local OB/FVG may arm
    -> first later footprint retest may enter

A failed or spent first structural retest terminates the setup.  No timeout,
volatility threshold or post-result score is introduced.
"""
from __future__ import annotations

from typing import Iterable

from domain import Candle
from easychart_mtf_scenario import MTFTradePlan
from easychart_zones import PriceZone
from market_structure import StructureEvent, StructurePath
from scenario_bundle_v4 import (
    StructuralSetup,
    StructuralSetupState,
    _EvidenceDetectorView,
)
from scenario_runtime_v4_source_trap import (
    SourceFaithfulRetestBundle,
    SourceFaithfulRetestStructuralScenarioEngine,
    SourceFaithfulSameSideBundle,
    SourceFaithfulSameSideStructuralScenarioEngine,
)


_ACCEPTANCE_PATHS = {
    StructurePath.ACCEPTANCE,
    StructurePath.CHANNEL_FAILURE_ACCEPTANCE,
}


class AcceptanceEntryGateMixin:
    """Synchronize accepted-break context confirmation and setup eligibility."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._acceptance_entry_ready: set[str] = set()
        self._acceptance_entry_wait_logged: set[str] = set()

    def _setup_id_for_event(self, event: StructureEvent) -> str:
        return f"{self.scale_name}:STRUCTURE:{event.event_id}"

    @staticmethod
    def _terminal_state_for_acceptance_reason(
        reason: str,
    ) -> StructuralSetupState:
        if "target" in reason:
            return StructuralSetupState.TARGET_SPENT
        if "first_retest_failed" in reason:
            return StructuralSetupState.FIRST_RETEST_UNRESOLVED
        if "no_target" in reason or "target_unavailable" in reason:
            return StructuralSetupState.NO_TARGET
        if "geometry" in reason:
            return StructuralSetupState.NO_TRADE_GEOMETRY
        return StructuralSetupState.INVALIDATED

    def _terminalize_acceptance_setup(
        self,
        event: StructureEvent,
        *,
        time_ns: int,
        reason: str,
        **values,
    ) -> None:
        setup_id = self._setup_id_for_event(event)
        setup = self._active.pop(setup_id, None)
        self._acceptance_entry_ready.discard(setup_id)
        self._acceptance_entry_wait_logged.discard(setup_id)
        if setup is None:
            return
        setup.state = self._terminal_state_for_acceptance_reason(reason)
        for zone in setup.trigger_zones:
            zone.consumed = True
        self._inc(f"acceptance_entry_gate_terminal_{reason}")
        self._trace(
            "acceptance_entry_gate_terminal",
            time_ns,
            setup,
            acceptance_gate_reason=reason,
            **values,
        )

    def _cancel_pending_acceptance(
        self,
        *,
        time_ns: int,
        reason: str,
        **values,
    ) -> None:
        event = self._pending_acceptance_context
        super()._cancel_pending_acceptance(
            time_ns=time_ns,
            reason=reason,
            **values,
        )
        if event is not None:
            self._terminalize_acceptance_setup(
                event,
                time_ns=time_ns,
                reason=reason,
                **values,
            )

    def _create_setups(self, events: Iterable[StructureEvent]) -> None:
        events = tuple(events)
        start = len(self.setups)
        super()._create_setups(events)
        pending = self._pending_acceptance_context
        for setup in self.setups[start:]:
            if (
                setup.event.path not in _ACCEPTANCE_PATHS
                or setup.state is not StructuralSetupState.WAITING_DISPLACEMENT
            ):
                continue
            if setup.setup_id in self._acceptance_entry_ready:
                continue
            if pending is None or pending.event_id != setup.event.event_id:
                self._terminalize_acceptance_setup(
                    setup.event,
                    time_ns=setup.event.interaction_time_ns,
                    reason="not_selected_by_context_arbitration",
                )
                continue
            self._inc("acceptance_setup_waiting_structural_retest")
            self._trace(
                "acceptance_setup_waiting_structural_retest",
                setup.event.interaction_time_ns,
                setup,
                context_boundary_id=setup.event.primary_boundary_id,
            )

    def _activate_context(
        self,
        event: StructureEvent,
        *,
        confirmed_time_ns: int,
        reference_price: float,
        reason: str,
    ) -> None:
        super()._activate_context(
            event,
            confirmed_time_ns=confirmed_time_ns,
            reference_price=reference_price,
            reason=reason,
        )
        active = self._active_context_event
        if (
            event.path not in _ACCEPTANCE_PATHS
            or active is None
            or active.event_id != event.event_id
        ):
            return
        setup_id = self._setup_id_for_event(event)
        setup = self._active.get(setup_id)
        if setup is None:
            return
        self._acceptance_entry_ready.add(setup_id)
        self._acceptance_entry_wait_logged.discard(setup_id)
        self._inc("acceptance_setup_structural_retest_confirmed")
        self._trace(
            "acceptance_setup_structural_retest_confirmed",
            confirmed_time_ns,
            setup,
            structural_retest_close=reference_price,
        )

    def _arm_displacement(
        self,
        setup: StructuralSetup,
        bar: Candle,
        index: int,
        created: Iterable[PriceZone],
    ) -> None:
        if (
            setup.event.path in _ACCEPTANCE_PATHS
            and setup.setup_id not in self._acceptance_entry_ready
        ):
            if setup.setup_id not in self._acceptance_entry_wait_logged:
                self._acceptance_entry_wait_logged.add(setup.setup_id)
                self._inc("acceptance_displacement_blocked_before_structural_retest")
                self._trace(
                    "acceptance_displacement_blocked_before_structural_retest",
                    bar.ts_close_ns,
                    setup,
                )
            return
        super()._arm_displacement(setup, bar, index, created)

    def _finish(
        self,
        setup: StructuralSetup,
        state: StructuralSetupState,
        bar: Candle,
        reason: str,
        **values,
    ) -> None:
        self._acceptance_entry_ready.discard(setup.setup_id)
        self._acceptance_entry_wait_logged.discard(setup.setup_id)
        super()._finish(setup, state, bar, reason, **values)

    def on_bar(
        self,
        timeframe_minutes: int,
        bar: Candle,
    ) -> list[MTFTradePlan]:
        if timeframe_minutes == self.context_minutes:
            return super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes != self.trigger_minutes:
            raise ValueError(f"unsupported structural timeframe {timeframe_minutes}")

        # Observe the complete closed lower bar first.  Its close may confirm
        # the structural S/R flip and its OB/FVG may be the event-local
        # displacement.  The setup can arm on this bar but, by construction,
        # cannot enter until a later footprint retest.
        created = self.trigger_detector.on_bar(bar)
        lower_events = self.structure.observe_lower_bar(bar)
        self._create_setups(lower_events)
        self._resolve_pending_acceptance_retest(bar)
        index = len(self.trigger_detector.bars) - 1
        return self._advance(bar, index, created)


class SourceFaithfulRetestEntryGatedEngine(
    AcceptanceEntryGateMixin,
    SourceFaithfulRetestStructuralScenarioEngine,
):
    """Conservative context policy with source-faithful entry ordering."""

    TRANSLATION_RULES = (
        SourceFaithfulRetestStructuralScenarioEngine.TRANSLATION_RULES
        + (
            "HUMAN_NATURAL_INFERENCE:ACCEPTANCE_SETUP_CANNOT_ARM_BEFORE_ITS_STRUCTURAL_RETEST_HOLDS",
            "HUMAN_NATURAL_INFERENCE:STRUCTURAL_RETEST_BAR_MAY_SUPPLY_DISPLACEMENT_BUT_NOT_ENTRY",
        )
    )


class SourceFaithfulSameSideEntryGatedEngine(
    AcceptanceEntryGateMixin,
    SourceFaithfulSameSideStructuralScenarioEngine,
):
    """Same-side context continuity with source-faithful entry ordering."""

    TRANSLATION_RULES = (
        SourceFaithfulSameSideStructuralScenarioEngine.TRANSLATION_RULES
        + (
            "HUMAN_NATURAL_INFERENCE:ACCEPTANCE_SETUP_CANNOT_ARM_BEFORE_ITS_STRUCTURAL_RETEST_HOLDS",
            "HUMAN_NATURAL_INFERENCE:STRUCTURAL_RETEST_BAR_MAY_SUPPLY_DISPLACEMENT_BUT_NOT_ENTRY",
        )
    )


def _initialize_bundle(
    bundle,
    *,
    engine_type,
    symbol: str,
    tick_size: float,
    minimum_gross_rr: float,
) -> None:
    bundle.symbol = symbol
    bundle.macro = engine_type(
        symbol,
        tick_size,
        scale_name="MACRO",
        context_minutes=60,
        trigger_minutes=5,
        minimum_gross_rr=minimum_gross_rr,
    )
    bundle.micro = engine_type(
        symbol,
        tick_size,
        scale_name="MICRO",
        context_minutes=15,
        trigger_minutes=1,
        minimum_gross_rr=minimum_gross_rr,
    )
    bundle.detectors = _EvidenceDetectorView(
        {
            60: bundle.macro.structure,
            15: bundle.micro.structure,
            5: bundle.macro.trigger_detector,
        },
        (bundle.micro.trigger_detector,),
    )
    bundle._claimed_episodes = set()
    bundle._bundle_trace = []
    bundle._routing_diagnostics = {}
    bundle._last_context_key = None


class SourceFaithfulRetestEntryGatedBundle(SourceFaithfulRetestBundle):
    """Conservative routing and accepted-break entry gate."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        _initialize_bundle(
            self,
            engine_type=SourceFaithfulRetestEntryGatedEngine,
            symbol=symbol,
            tick_size=tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )


class SourceFaithfulSameSideEntryGatedBundle(SourceFaithfulSameSideBundle):
    """Same-side context continuity and accepted-break entry gate."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        _initialize_bundle(
            self,
            engine_type=SourceFaithfulSameSideEntryGatedEngine,
            symbol=symbol,
            tick_size=tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )


__all__ = [
    "AcceptanceEntryGateMixin",
    "SourceFaithfulRetestEntryGatedBundle",
    "SourceFaithfulRetestEntryGatedEngine",
    "SourceFaithfulSameSideEntryGatedBundle",
    "SourceFaithfulSameSideEntryGatedEngine",
]
