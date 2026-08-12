"""Source-grounded refinements for the EasyChart v4 scene router.

The PDF/video material treats an accepted break as an S/R-flip candidate, not
as immediate directional permission. A human trader naturally waits for the
broken boundary to be revisited and to hold from the new side before using it
as context for a lower-timeframe entry. This module makes that previously
implicit step causal and auditable:

    context close outside -> next context bar holds outside
    -> first later lower-timeframe retest of the broken boundary
    -> close holds on the new side
    -> context becomes live

Missing the level means no trade. A failed first retest consumes the event;
the code never waits for a prettier second retest.
"""
from __future__ import annotations

from typing import Iterable

from domain import Candle, Side
from easychart_mtf_scenario import MTFTradePlan
from market_structure import StructureEvent, StructurePath
from scenario_bundle_v4 import (
    StructuralScenarioEngine,
    StructuralSetupState,
    _EvidenceDetectorView,
)
from scenario_runtime_v4 import (
    CausalStructuralScenarioEngine as _BaseCausalStructuralScenarioEngine,
    ResearchScenarioBundleV4 as _BaseResearchScenarioBundleV4,
)


_ACCEPTANCE_PATHS = {
    StructurePath.ACCEPTANCE,
    StructurePath.CHANNEL_FAILURE_ACCEPTANCE,
}


class RetestConfirmedStructuralScenarioEngine(_BaseCausalStructuralScenarioEngine):
    """Delay accepted-break context until its first later S/R-flip retest."""

    SOURCE_RULES = _BaseCausalStructuralScenarioEngine.SOURCE_RULES + (
        "SOURCE_EXPLICIT:BREAKOUT_ENTRY_USES_RETEST_OF_BROKEN_STRUCTURE",
        "SOURCE_EXPLICIT:MISSED_ENTRY_LEVEL_MEANS_NO_TRADE",
    )
    TRANSLATION_RULES = _BaseCausalStructuralScenarioEngine.TRANSLATION_RULES + (
        "HUMAN_NATURAL_INFERENCE:ACCEPTED_BREAK_IS_UNRESOLVED_UNTIL_FIRST_LATER_SR_FLIP_RETEST_HOLDS",
        "HUMAN_NATURAL_INFERENCE:LOWER_TIMEFRAME_MAY_CONFIRM_RETEST_AFTER_CONTEXT_ACCEPTANCE_IS_KNOWN",
        "HUMAN_NATURAL_INFERENCE:FAILED_FIRST_SR_FLIP_RETEST_CONSUMES_THE_BREAKOUT_CONTEXT",
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pending_acceptance_context: StructureEvent | None = None
        self._pending_acceptance_time_ns: int | None = None

    def _cancel_pending_acceptance(
        self,
        *,
        time_ns: int,
        reason: str,
        **values,
    ) -> None:
        event = self._pending_acceptance_context
        if event is None:
            return
        self._inc(reason)
        boundary = self.structure.find_boundary(event.primary_boundary_id)
        self._trace(
            reason,
            time_ns,
            event_id=event.event_id,
            context_side=event.side.name,
            context_path=event.path.value,
            context_structure_kind=event.structure_kind.value,
            context_boundary_id=event.primary_boundary_id,
            context_boundary_level=(
                None if boundary is None else boundary.level_at(time_ns)
            ),
            context_stop=event.stop_reference,
            context_target=self._context_target(event, time_ns),
            **values,
        )
        self._pending_acceptance_context = None
        self._pending_acceptance_time_ns = None

    def _arm_pending_acceptance(self, event: StructureEvent) -> None:
        target = self._context_target(event, event.interaction_time_ns)
        reference = event.reference_close
        valid = (
            target is not None
            and (
                event.stop_reference < reference < target
                if event.side is Side.LONG
                else target < reference < event.stop_reference
            )
        )
        if not valid:
            self._inc("context_acceptance_retest_rejected_invalid_geometry")
            self._trace(
                "context_acceptance_retest_rejected_invalid_geometry",
                event.interaction_time_ns,
                event_id=event.event_id,
                context_side=event.side.name,
                context_path=event.path.value,
                context_structure_kind=event.structure_kind.value,
                context_stop=event.stop_reference,
                context_target=target,
                reference_price=reference,
            )
            return

        previous_pending = self._pending_acceptance_context
        if previous_pending is not None and previous_pending.event_id != event.event_id:
            self._cancel_pending_acceptance(
                time_ns=event.interaction_time_ns,
                reason="context_pending_acceptance_superseded",
                replacement_event_id=event.event_id,
                replacement_side=event.side.name,
            )
        if self._active_context_event is not None:
            self._clear_context(
                time_ns=event.interaction_time_ns,
                reason="context_replaced_by_unresolved_acceptance",
                replacement_event_id=event.event_id,
                replacement_side=event.side.name,
            )
        self._pending_acceptance_context = event
        self._pending_acceptance_time_ns = event.interaction_time_ns
        boundary = self.structure.find_boundary(event.primary_boundary_id)
        self._inc("context_acceptance_waiting_first_retest")
        self._trace(
            "context_acceptance_waiting_first_retest",
            event.interaction_time_ns,
            event_id=event.event_id,
            context_side=event.side.name,
            context_path=event.path.value,
            context_structure_kind=event.structure_kind.value,
            context_boundary_id=event.primary_boundary_id,
            context_boundary_level=(
                None
                if boundary is None
                else boundary.level_at(event.interaction_time_ns)
            ),
            context_stop=event.stop_reference,
            context_target=target,
            reference_price=reference,
        )

    def _activate_context(
        self,
        event: StructureEvent,
        *,
        confirmed_time_ns: int,
        reference_price: float,
        reason: str,
    ) -> None:
        pending = self._pending_acceptance_context
        if pending is not None and pending.event_id != event.event_id:
            self._cancel_pending_acceptance(
                time_ns=confirmed_time_ns,
                reason="context_pending_acceptance_superseded_by_confirmed_event",
                replacement_event_id=event.event_id,
                replacement_side=event.side.name,
            )
        super()._activate_context(
            event,
            confirmed_time_ns=confirmed_time_ns,
            reference_price=reference_price,
            reason=reason,
        )

    def _resolve_pending_acceptance_retest(self, bar: Candle) -> None:
        event = self._pending_acceptance_context
        accepted_time = self._pending_acceptance_time_ns
        if event is None or accepted_time is None or bar.ts_close_ns <= accepted_time:
            return
        boundary = self.structure.find_boundary(event.primary_boundary_id)
        if boundary is None:
            self._cancel_pending_acceptance(
                time_ns=bar.ts_close_ns,
                reason="context_acceptance_boundary_unavailable",
            )
            return
        target = self._context_target(event, bar.ts_close_ns)
        if target is None:
            self._cancel_pending_acceptance(
                time_ns=bar.ts_close_ns,
                reason="context_acceptance_target_unavailable",
            )
            return

        stop_hit = (
            bar.low <= event.stop_reference
            if event.side is Side.LONG
            else bar.high >= event.stop_reference
        )
        target_hit = (
            bar.high >= target
            if event.side is Side.LONG
            else bar.low <= target
        )
        if stop_hit or target_hit:
            reason = (
                "context_acceptance_stop_and_target_before_retest"
                if stop_hit and target_hit
                else "context_acceptance_stop_before_retest"
                if stop_hit
                else "context_acceptance_target_spent_before_retest"
            )
            self._cancel_pending_acceptance(
                time_ns=bar.ts_close_ns,
                reason=reason,
                bar_low=bar.low,
                bar_high=bar.high,
            )
            return

        level = boundary.level_at(bar.ts_close_ns)
        half_tick = self.tick_size / 2.0
        touched = (
            bar.low <= level + half_tick
            if event.side is Side.LONG
            else bar.high >= level - half_tick
        )
        if not touched:
            return

        held = (
            bar.close > level + half_tick
            if event.side is Side.LONG
            else bar.close < level - half_tick
        )
        if not held:
            self._cancel_pending_acceptance(
                time_ns=bar.ts_close_ns,
                reason="context_acceptance_first_retest_failed",
                boundary_level=level,
                retest_open=bar.open,
                retest_high=bar.high,
                retest_low=bar.low,
                retest_close=bar.close,
            )
            return

        self._pending_acceptance_context = None
        self._pending_acceptance_time_ns = None
        self._activate_context(
            event,
            confirmed_time_ns=bar.ts_close_ns,
            reference_price=bar.close,
            reason="context_acceptance_first_retest_confirmed",
        )

    def _create_setups(self, events) -> None:
        events = tuple(events)
        start = len(self.setups)
        # Bypass the old context-activation policy while preserving the source
        # scenario setup creation itself.
        StructuralScenarioEngine._create_setups(self, events)
        new_setups = self.setups[start:]
        confirmed_non_fakeout: list[StructureEvent] = []
        for setup in new_setups:
            if setup.state is not StructuralSetupState.WAITING_DISPLACEMENT:
                continue
            if setup.event.path is StructurePath.FAKEOUT:
                interaction = self.structure.bars[setup.event.interaction_index]
                level = (
                    interaction.high
                    if setup.event.side is Side.LONG
                    else interaction.low
                )
                self._pending_fakeout_confirmation[setup.setup_id] = level
                self._inc("fakeout_reversal_confirmation_required")
                self._trace(
                    "fakeout_reversal_confirmation_required",
                    setup.event.interaction_time_ns,
                    setup,
                    reversal_confirmation_price=level,
                )
            else:
                confirmed_non_fakeout.append(setup.event)

        sides = {event.side for event in confirmed_non_fakeout}
        if len(sides) > 1:
            if self._active_context_event is not None:
                self._clear_context(
                    time_ns=max(
                        event.interaction_time_ns
                        for event in confirmed_non_fakeout
                    ),
                    reason="context_conflicting_events_same_close",
                    conflicting_event_ids=[
                        event.event_id for event in confirmed_non_fakeout
                    ],
                )
            self._cancel_pending_acceptance(
                time_ns=max(
                    event.interaction_time_ns
                    for event in confirmed_non_fakeout
                ),
                reason="context_pending_acceptance_cleared_by_conflict",
                conflicting_event_ids=[
                    event.event_id for event in confirmed_non_fakeout
                ],
            )
            self._inc("context_conflicting_events_same_close_observed")
            return
        if not confirmed_non_fakeout:
            return

        # Detector order is semantic: channel, trendline, horizontal swing.
        event = confirmed_non_fakeout[0]
        if event.path in _ACCEPTANCE_PATHS:
            self._arm_pending_acceptance(event)
        else:
            self._activate_context(
                event,
                confirmed_time_ns=event.interaction_time_ns,
                reference_price=event.reference_close,
                reason="context_structural_event_activated",
            )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        plans = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes == self.trigger_minutes:
            self._resolve_pending_acceptance_retest(bar)
        return plans


class ResearchScenarioBundleV4(_BaseResearchScenarioBundleV4):
    """Use retest-confirmed higher context while preserving one policy/router."""

    TOP_DOWN_TRANSLATION_RULES = (
        "HUMAN_NATURAL_INFERENCE:LATEST_LIVE_CONFIRMED_1H_STRUCTURAL_EVENT_DEFINES_MEDIUM_STATE",
        "HUMAN_NATURAL_INFERENCE:1H_CONTEXT_PERSISTS_UNTIL_ITS_STRUCTURAL_STOP_OR_OBJECTIVE",
        "SOURCE_EXPLICIT:ACCEPTED_BREAK_REQUIRES_FIRST_LATER_SR_FLIP_RETEST",
        "SOURCE_EXPLICIT:MISSED_ENTRY_LEVEL_MEANS_NO_TRADE",
        "HUMAN_NATURAL_INFERENCE:UNRESOLVED_HIGHER_EVENT_CONTEXT_MEANS_NO_MICRO_TRADE",
    )

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.macro = RetestConfirmedStructuralScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = RetestConfirmedStructuralScenarioEngine(
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
        self._routing_diagnostics: dict[str, int] = {}
        self._last_context_key: tuple[str | None, str] | None = None


__all__ = [
    "RetestConfirmedStructuralScenarioEngine",
    "ResearchScenarioBundleV4",
]
