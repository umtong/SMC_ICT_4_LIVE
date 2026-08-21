"""High-conviction sweep/reclaim absorption with the first micro objective.

A liquidity sweep is not complete merely because aggressive orders appeared on
the wrong side and price closed back through a line.  The source describes a
more specific transfer of control: stop orders supply substantial aggression,
that aggression fails to continue price through the boundary, and the completed
response closes decisively back inside the intended auction.

This module gives flow-only substitution that exact responsibility:

* the current completed minute must already satisfy the inherited boundary
  penetration, full reclaim, active-volume and directed-flow conditions;
* at least sixty percent of quote flow must be aggressive against the intended
  trade (absolute signed taker share >= 0.20);
* the response body must point in the intended direction;
* the close must finish in the favorable outer quartile of its range;
* repeated/stale absorption cannot originate a trade;
* visual event-local OB/FVG first-return entries remain available;
* only REJECTION paths reach the account and the full-position objective is the
  first pre-existing untouched high-quality one-minute opposing OB/FVG when it
  is nearer than the inherited 5m/15m objective.

The 60/40 split and outer quartile are interpretable auction geometry, not a PnL
score.  No fitted composite, risk multiplier, time-of-day rule, partial exit or
post-entry management is added.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Side
from easychart_re1_flow import FlowObservation, FlowSignal
from easychart_re1_rejection_micro_target_v2 import (
    EasyChartRE1RejectionMicroTargetV2Bundle,
    FixedRejectionTargetDecisionOBEngine,
    FixedRejectionTargetDirectSweepEngine,
    FixedRejectionTargetMajorSwingEngine,
    FixedRejectionTargetMicroEngine,
)


STRONG_ABSORPTION_TRANSFER_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "FLOW_ONLY_SWEEP_RECLAIM_REQUIRES_AT_LEAST_SIXTY_PERCENT_ADVERSE_TAKER_FLOW_AND_A_FAVORABLE_OUTER_QUARTILE_RESPONSE_CLOSE"
)
if STRONG_ABSORPTION_TRANSFER_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (STRONG_ABSORPTION_TRANSFER_RULE,)


class StrongAbsorptionTransferMixin:
    """Accept only a decisive current-bar transfer from adverse flow to price."""

    MIN_ADVERSE_DELTA_SHARE = 0.20
    FAVORABLE_CLOSE_FRACTION = 0.75

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
            self._finc("noncurrent_absorption_kept_diagnostic_only")
            return None
        if observation is None:
            raise RuntimeError("strong absorption lost its current flow observation")

        adverse_share = (
            -observation.delta_share
            if setup.side is Side.LONG
            else observation.delta_share
        )
        aligned_body = (
            observation.body > 0.0
            if setup.side is Side.LONG
            else observation.body < 0.0
        )
        decisive_close = (
            observation.close_location >= self.FAVORABLE_CLOSE_FRACTION
            if setup.side is Side.LONG
            else observation.close_location <= 1.0 - self.FAVORABLE_CLOSE_FRACTION
        )
        if (
            adverse_share + 1e-12 < self.MIN_ADVERSE_DELTA_SHARE
            or not aligned_body
            or not decisive_close
        ):
            self._finc("current_absorption_without_decisive_transfer_deferred")
            self._trace(
                "current_absorption_without_decisive_transfer_deferred",
                bar.ts_close_ns,
                setup,
                adverse_delta_share=adverse_share,
                body=observation.body,
                close_location=observation.close_location,
                required_adverse_delta_share=self.MIN_ADVERSE_DELTA_SHARE,
                required_favorable_close_fraction=self.FAVORABLE_CLOSE_FRACTION,
                activity_ratio=observation.activity_ratio,
                delta_ratio=observation.delta_ratio,
                body_ratio=observation.body_ratio,
                rule_provenance=STRONG_ABSORPTION_TRANSFER_RULE,
            )
            return None

        self._finc("strong_current_absorption_transfer_confirmed")
        self._trace(
            "strong_current_absorption_transfer_confirmed",
            bar.ts_close_ns,
            setup,
            adverse_delta_share=adverse_share,
            body=observation.body,
            close_location=observation.close_location,
            activity_ratio=observation.activity_ratio,
            delta_ratio=observation.delta_ratio,
            body_ratio=observation.body_ratio,
            range_ratio=observation.range_ratio,
            impact_per_activity=observation.impact_per_activity,
            rule_provenance=STRONG_ABSORPTION_TRANSFER_RULE,
        )
        return signal

    @property
    def strong_absorption_diagnostics(self) -> dict[str, Any]:
        return {
            "minimum_adverse_delta_share": self.MIN_ADVERSE_DELTA_SHARE,
            "favorable_close_fraction": self.FAVORABLE_CLOSE_FRACTION,
            "repeated_absorption": "DIAGNOSTIC_ONLY",
            "rule_provenance": STRONG_ABSORPTION_TRANSFER_RULE,
        }


class StrongAbsorptionMicroEngine(
    StrongAbsorptionTransferMixin,
    FixedRejectionTargetMicroEngine,
):
    pass


class StrongAbsorptionMajorSwingEngine(
    StrongAbsorptionTransferMixin,
    FixedRejectionTargetMajorSwingEngine,
):
    pass


class StrongAbsorptionDecisionOBEngine(
    StrongAbsorptionTransferMixin,
    FixedRejectionTargetDecisionOBEngine,
):
    pass


class StrongAbsorptionDirectSweepEngine(
    StrongAbsorptionTransferMixin,
    FixedRejectionTargetDirectSweepEngine,
):
    pass


class EasyChartRE1StrongAbsorptionTargetBundle(
    EasyChartRE1RejectionMicroTargetV2Bundle,
):
    """Rejection-only visual/flow core with decisive flow substitution."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = StrongAbsorptionMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = StrongAbsorptionMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = StrongAbsorptionDecisionOBEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.direct_sweep_ob = StrongAbsorptionDirectSweepEngine(
            symbol,
            tick_size,
            scale_name="DIRECT_SWEEP_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "major_swing", "flow_decision_ob", "direct_sweep_ob"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["strong_absorption_transfer_policy"] = {
            "micro": self.micro.strong_absorption_diagnostics,
            "major_swing": self.major_swing.strong_absorption_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.strong_absorption_diagnostics,
            "direct_sweep_ob": self.direct_sweep_ob.strong_absorption_diagnostics,
            "rule_provenance": STRONG_ABSORPTION_TRANSFER_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1StrongAbsorptionTargetBundle
