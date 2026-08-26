"""Single-owner entry responsibility for the liquidity-sweep flow-OB system.

A completed one-minute candle cannot simultaneously create a valid event-local
OB/FVG and be treated as a flow substitute for the missing footprint.  Once the
visual footprint exists, its future first return owns the entry decision.  Flow
substitution remains available only when the current auction produced no such
footprint; accepted-break exact-retest flow remains unchanged.

This is not a volume threshold.  It resolves two competing explanations of the
same completed candle and prevents one bar from both defining a future support
or resistance zone and skipping the mitigation which gives that zone meaning.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState
from easychart_re1_flow import FlowEntryMixin
from easychart_re1_flow_focused import FocusedAuctionFlowMixin
from easychart_re1_flow_ob import (
    FLOW_OB_FIRST_TOUCH_RULE,
    FLOW_VALIDATED_OB_FORMATION_RULE,
)
from easychart_re1_flow_ob_sweep import (
    CAUSAL_SWING_LIQUIDITY_PROXY_RULE,
    LIQUIDITY_TAKING_OB_RULE,
    EasyChartRE1SweepFlowOBBundle,
    LiquiditySweepFlowDecisionAreaEngine,
    LiquiditySweepFlowValidatedOBBook,
)
from easychart_re1_natural_geometry import NaturalMajorSwingEngine, NaturalMicroEngine
from easychart_re1_phase import ChannelPhaseStructureBook


SINGLE_ENTRY_EVIDENCE_OWNER_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "WHEN_THE_CURRENT_BAR_CREATES_A_VALID_EVENT_LOCAL_OB_OR_FVG_THE_FUTURE_FIRST_RETURN_OWNS_ENTRY_AND_SAME_BAR_FLOW_CANNOT_SKIP_MITIGATION"
)
if SINGLE_ENTRY_EVIDENCE_OWNER_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (SINGLE_ENTRY_EVIDENCE_OWNER_RULE,)


class VisualFootprintOwnsCurrentBarFlowMixin(FocusedAuctionFlowMixin):
    """Use flow as a genuine missing-footprint substitute, never an accelerator."""

    def _arm_displacements(
        self,
        bar: Any,
        index: int,
        created: list[Any],
    ) -> None:
        self._flow_current = self.flow_analyzer.observe(bar)
        candidates = [
            setup
            for setup in list(self._active.values())
            if setup.state is SetupState.WAITING_DISPLACEMENT
            and setup.confirmation_time_ns is not None
            and bar.ts_close_ns > setup.confirmation_time_ns
        ]
        signals = {
            setup.setup_id: self._flow_signal(setup, bar, self._flow_current)
            for setup in candidates
        }

        # Skip FlowEntryMixin's unrestricted same-bar accelerator while running
        # the complete inherited visual footprint state machine exactly once.
        super(FlowEntryMixin, self)._arm_displacements(bar, index, created)

        for original in candidates:
            signal = signals.get(original.setup_id)
            if signal is None:
                continue
            setup = self._active.get(original.setup_id)
            if setup is None:
                # A complete visual direct entry or terminal geometry already
                # resolved this episode and retains priority.
                self._finc("visual_or_terminal_resolution_preceded_flow")
                continue
            if setup.state is SetupState.WAITING_FOOTPRINT_RETEST:
                self._finc("same_bar_visual_footprint_owns_future_retest")
                self._trace(
                    "same_bar_visual_footprint_owns_future_retest",
                    bar.ts_close_ns,
                    setup,
                    flow_kind=signal.kind.value,
                    flow_mechanism=signal.mechanism,
                    trigger_zone_id=(
                        None if setup.trigger_zone is None else setup.trigger_zone.zone_id
                    ),
                    rule_provenance=SINGLE_ENTRY_EVIDENCE_OWNER_RULE,
                )
                continue
            if setup.state is not SetupState.WAITING_DISPLACEMENT:
                self._finc("flow_candidate_resolved_by_visual_state_machine")
                continue
            self._create_flow_plan(setup, bar, signal, acceptance=False)

    @property
    def entry_owner_diagnostics(self) -> dict[str, Any]:
        return {
            "missing_visual_footprint": "FLOW_SUBSTITUTION_ALLOWED",
            "current_bar_visual_footprint": "FUTURE_FIRST_RETURN_OWNS_ENTRY",
            "accepted_break": "INHERITED_EXACT_RETEST_FLOW_POLICY",
            "rule_provenance": SINGLE_ENTRY_EVIDENCE_OWNER_RULE,
        }


class ResponsiblePhaseFlowMicroEngine(
    VisualFootprintOwnsCurrentBarFlowMixin,
    NaturalMicroEngine,
):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = ChannelPhaseStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )


class ResponsibleFlowMajorSwingEngine(
    VisualFootprintOwnsCurrentBarFlowMixin,
    NaturalMajorSwingEngine,
):
    pass


class ResponsibleLiquiditySweepFlowDecisionAreaEngine(
    VisualFootprintOwnsCurrentBarFlowMixin,
    LiquiditySweepFlowDecisionAreaEngine,
):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = LiquiditySweepFlowValidatedOBBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
            self.flow_analyzer,
        )


class EasyChartRE1ResponsibleSweepFlowOBBundle(EasyChartRE1SweepFlowOBBundle):
    """One integrated account stream with unambiguous entry evidence ownership."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = ResponsiblePhaseFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = ResponsibleFlowMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = ResponsibleLiquiditySweepFlowDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "major_swing", "flow_decision_ob"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["single_entry_evidence_owner"] = {
            "micro": self.micro.entry_owner_diagnostics,
            "major_swing": self.major_swing.entry_owner_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.entry_owner_diagnostics,
            "rules": (
                SINGLE_ENTRY_EVIDENCE_OWNER_RULE,
                LIQUIDITY_TAKING_OB_RULE,
                CAUSAL_SWING_LIQUIDITY_PROXY_RULE,
                FLOW_VALIDATED_OB_FORMATION_RULE,
                FLOW_OB_FIRST_TOUCH_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ResponsibleSweepFlowOBBundle
