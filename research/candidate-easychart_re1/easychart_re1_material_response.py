"""Material price response for flow-only EasyChart RE1 reversal substitution.

A large adverse taker imbalance at a decision boundary proves that liquidity was
available; it does not by itself prove that the intended side took control.
Across the expanded diagnostics, winning flow substitutions had materially
larger response bodies and much higher price impact per unit activity than
losers, while visual OB/FVG first-return entries did not need this extra rule.

This module therefore changes one semantic responsibility only:

* a *current-bar* sweep/reclaim absorption may replace a missing visual
  footprint only when the completed one-minute response body is at least the
  causal median absolute body of the previous sixty completed minutes;
* an existing visual OB/FVG, its first return, accepted-break flow, repeated
  absorption diagnostics, stops, targets, costs and account routing are
  unchanged;
* channel-edge reversals remain diagnostic-only under the current quality core.

The threshold is not fitted from PnL.  It is the existing
``FlowObservation.material_progress`` definition already used to distinguish
initiative from noise.  The rule asks price to confirm that absorbed volume
created a non-trivial state transition before flow is allowed to substitute for
a missing footprint.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from easychart_re1_channel_abstention import (
    ChannelAbstainingMicroEngine,
    EasyChartRE1ChannelAbstentionBundle,
)
from easychart_re1_flow import FlowObservation, FlowSignal
from easychart_re1_flow_ob_responsibility import (
    ResponsibleFlowValidatedDecisionAreaEngine,
)
from easychart_re1_flow_ob_sweep_responsibility import (
    ResponsibleFlowMajorSwingEngine,
)


MATERIAL_RESPONSE_SUBSTITUTION_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "CURRENT_SWEEP_RECLAIM_ABSORPTION_MAY_REPLACE_A_MISSING_VISUAL_FOOTPRINT_ONLY_WHEN_THE_COMPLETED_ONE_MINUTE_RESPONSE_HAS_CAUSALLY_MATERIAL_PRICE_PROGRESS"
)
if MATERIAL_RESPONSE_SUBSTITUTION_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (MATERIAL_RESPONSE_SUBSTITUTION_RULE,)


class MaterialResponseAbsorptionMixin:
    """Reject flow-only current absorption whose price response is still noise."""

    def _reversal_absorption_signal(
        self,
        setup: Any,
        bar: Any,
        observation: FlowObservation | None,
    ) -> FlowSignal | None:
        signal = super()._reversal_absorption_signal(setup, bar, observation)
        if signal is None:
            return None
        if signal.mechanism != "SWEEP_RECLAIM_CURRENT_ABSORPTION":
            return signal
        if observation is None:
            raise RuntimeError("current absorption signal lost its flow observation")
        if observation.material_progress:
            self._finc("current_absorption_material_price_response_confirmed")
            self._trace(
                "current_absorption_material_price_response_confirmed",
                bar.ts_close_ns,
                setup,
                body=observation.body,
                median_abs_body=observation.median_abs_body,
                body_ratio=observation.body_ratio,
                activity_ratio=observation.activity_ratio,
                delta_ratio=observation.delta_ratio,
                impact_per_activity=observation.impact_per_activity,
                flow_kind=signal.kind.value,
                rule_provenance=MATERIAL_RESPONSE_SUBSTITUTION_RULE,
            )
            return signal
        self._finc("current_absorption_without_material_price_response_deferred")
        self._trace(
            "current_absorption_without_material_price_response_deferred",
            bar.ts_close_ns,
            setup,
            body=observation.body,
            median_abs_body=observation.median_abs_body,
            body_ratio=observation.body_ratio,
            activity_ratio=observation.activity_ratio,
            delta_ratio=observation.delta_ratio,
            impact_per_activity=observation.impact_per_activity,
            flow_kind=signal.kind.value,
            rule_provenance=MATERIAL_RESPONSE_SUBSTITUTION_RULE,
        )
        # Keep the setup alive.  A later visual footprint may still own entry.
        return None

    @property
    def material_response_diagnostics(self) -> dict[str, Any]:
        return {
            "flow_only_current_absorption": "REQUIRES_EXISTING_CAUSAL_MATERIAL_PROGRESS",
            "visual_footprint": "UNCHANGED",
            "rule_provenance": MATERIAL_RESPONSE_SUBSTITUTION_RULE,
        }


class MaterialResponseMicroEngine(
    MaterialResponseAbsorptionMixin,
    ChannelAbstainingMicroEngine,
):
    pass


class MaterialResponseMajorSwingEngine(
    MaterialResponseAbsorptionMixin,
    ResponsibleFlowMajorSwingEngine,
):
    pass


class MaterialResponseDecisionOBEngine(
    MaterialResponseAbsorptionMixin,
    ResponsibleFlowValidatedDecisionAreaEngine,
):
    pass


class EasyChartRE1MaterialResponseBundle(EasyChartRE1ChannelAbstentionBundle):
    """Channel-abstaining quality core with non-trivial flow substitution."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = MaterialResponseMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = MaterialResponseMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = MaterialResponseDecisionOBEngine(
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
        output["material_response_flow_substitution"] = {
            "micro": self.micro.material_response_diagnostics,
            "major_swing": self.major_swing.material_response_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.material_response_diagnostics,
            "rule_provenance": MATERIAL_RESPONSE_SUBSTITUTION_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1MaterialResponseBundle
