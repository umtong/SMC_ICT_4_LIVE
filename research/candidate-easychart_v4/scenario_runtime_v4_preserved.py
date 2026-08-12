"""Preserve a live same-side context while a newer S/R flip is unresolved.

EasyChart's top-down workflow treats a confirmed higher-timeframe reaction as
the working scene until its own structural stop, objective, or an actually
confirmed contrary transition ends it. A later accepted break in the same
direction is therefore a possible continuation update, not an immediate reason
to erase the already-confirmed scene before its first retest.

An opposite-side accepted break remains genuinely ambiguous and suspends the
old context until a new scenario confirms. This distinction restores the human
behaviour that was lost when every provisional acceptance cleared context.
"""
from __future__ import annotations

from domain import Side
from market_structure import StructureEvent
from scenario_bundle_v4 import _EvidenceDetectorView
from scenario_runtime_v4_refined import (
    ResearchScenarioBundleV4 as _BaseResearchScenarioBundleV4,
    RetestConfirmedStructuralScenarioEngine,
)


class SameSidePreservingStructuralScenarioEngine(
    RetestConfirmedStructuralScenarioEngine,
):
    """Keep confirmed same-side context while a continuation retest is pending."""

    TRANSLATION_RULES = RetestConfirmedStructuralScenarioEngine.TRANSLATION_RULES + (
        "HUMAN_NATURAL_INFERENCE:SAME_SIDE_PROVISIONAL_ACCEPTANCE_DOES_NOT_ERASE_CONFIRMED_CONTEXT",
        "HUMAN_NATURAL_INFERENCE:OPPOSITE_PROVISIONAL_ACCEPTANCE_SUSPENDS_OLD_CONTEXT",
    )

    def _arm_pending_acceptance(self, event: StructureEvent) -> None:
        active = self._active_context_event
        if active is None or active.side is not event.side:
            super()._arm_pending_acceptance(event)
            return

        confirmed_time = self._active_context_confirmed_time_ns
        basis = self._active_context_basis
        # The parent correctly creates/cancels the pending acceptance, but its
        # conservative baseline clears every active context. Temporarily hide
        # this one only because it is already confirmed and points in the same
        # direction; always restore it, including invalid-geometry paths.
        self._active_context_event = None
        self._active_context_confirmed_time_ns = None
        self._active_context_basis = "UNRESOLVED_1H_EVENT_CONTEXT"
        try:
            super()._arm_pending_acceptance(event)
        finally:
            self._active_context_event = active
            self._active_context_confirmed_time_ns = confirmed_time
            self._active_context_basis = basis

        pending = self._pending_acceptance_context
        if pending is not None and pending.event_id == event.event_id:
            self._inc("context_same_side_acceptance_pending_without_suspension")
            self._trace(
                "context_same_side_acceptance_pending_without_suspension",
                event.interaction_time_ns,
                event_id=active.event_id,
                context_side=active.side.name,
                context_path=active.path.value,
                context_structure_kind=active.structure_kind.value,
                continuation_event_id=event.event_id,
                continuation_side=event.side.name,
                continuation_path=event.path.value,
                continuation_structure_kind=event.structure_kind.value,
            )


class ResearchScenarioBundleV4(_BaseResearchScenarioBundleV4):
    """Retest-confirmed hierarchy with same-side context continuity."""

    TOP_DOWN_TRANSLATION_RULES = (
        "HUMAN_NATURAL_INFERENCE:LATEST_LIVE_CONFIRMED_1H_STRUCTURAL_EVENT_DEFINES_MEDIUM_STATE",
        "HUMAN_NATURAL_INFERENCE:1H_CONTEXT_PERSISTS_UNTIL_ITS_STRUCTURAL_STOP_OR_OBJECTIVE",
        "SOURCE_EXPLICIT:ACCEPTED_BREAK_REQUIRES_FIRST_LATER_SR_FLIP_RETEST",
        "SOURCE_EXPLICIT:MISSED_ENTRY_LEVEL_MEANS_NO_TRADE",
        "HUMAN_NATURAL_INFERENCE:SAME_SIDE_PROVISIONAL_ACCEPTANCE_PRESERVES_CONFIRMED_CONTEXT",
        "HUMAN_NATURAL_INFERENCE:OPPOSITE_PROVISIONAL_ACCEPTANCE_SUSPENDS_OLD_CONTEXT",
        "HUMAN_NATURAL_INFERENCE:UNRESOLVED_HIGHER_EVENT_CONTEXT_MEANS_NO_MICRO_TRADE",
    )

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.macro = SameSidePreservingStructuralScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = SameSidePreservingStructuralScenarioEngine(
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
    "SameSidePreservingStructuralScenarioEngine",
    "ResearchScenarioBundleV4",
]
