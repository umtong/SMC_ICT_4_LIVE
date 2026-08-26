"""Decision-frame control transfer for flow-only EasyChart RE1 reversals.

The immediate one-minute sweep/reclaim detector is useful evidence that adverse
aggression met passive liquidity, but it does not yet prove that control changed
hands. A visual event-local OB/FVG keeps its existing first-return ownership.
Only when no complete visual footprint formed may flow substitute for it, and
then the auction must survive through a completed five-minute decision frame.

The completed frame must reclaim both the projected boundary and the midpoint
of the original five-minute interaction range, avoid a new adverse extreme, and
show one of two internally-scaled transfer mechanisms:

* continued adverse taker flow while price advances in the intended direction
  (passive absorption), or
* intended taker flow whose recovery impact per signed quote exceeds the
  adverse sweep impact per signed quote (active re-initiative).

No absolute volume threshold, clock timeout, score, session rule, fitted
percentile, partial exit, stop movement or fixed-R target is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from domain import Side
from easychart_re1_flow import FlowObservation, FlowSignal, FlowTriggerKind
from easychart_re1_rejection_micro_target_v2 import (
    EasyChartRE1RejectionMicroTargetV2Bundle,
    FixedRejectionTargetDecisionOBEngine,
    FixedRejectionTargetDirectSweepEngine,
    FixedRejectionTargetMajorSwingEngine,
    FixedRejectionTargetMicroEngine,
)


DECISION_FRAME_CONTROL_TRANSFER_RULE = (
    "EXTERNAL_METHOD:"
    "FLOW_ONLY_REVERSAL_WAITS_FOR_COMPLETED_DECISION_FRAME_AND_REQUIRES_BOUNDARY_AND_INTERACTION_BALANCE_RECLAIM_WITHOUT_A_NEW_ADVERSE_EXTREME"
)
IMPACT_EFFICIENCY_TRANSFER_RULE = (
    "EXTERNAL_METHOD:"
    "CONTROL_TRANSFERS_WHEN_PRICE_ADVANCES_AGAINST_CONTINUED_ADVERSE_FLOW_OR_RECOVERY_IMPACT_PER_SIGNED_QUOTE_EXCEEDS_SWEEP_IMPACT_PER_SIGNED_QUOTE"
)
for _rule in (DECISION_FRAME_CONTROL_TRANSFER_RULE, IMPACT_EFFICIENCY_TRANSFER_RULE):
    if _rule not in _contracts.EXTERNAL_RULES:
        _contracts.EXTERNAL_RULES += (_rule,)


@dataclass(slots=True)
class PendingControlTransfer:
    sweep_time_ns: int
    sweep_extreme: float
    boundary_lower: float
    boundary_upper: float
    adverse_signed_quote: float
    signal_kind: FlowTriggerKind
    interaction_midpoint: float


class DecisionFrameControlTransferMixin:
    """Turn immediate absorption into a pending causal control-transfer test."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_control_transfers: dict[str, PendingControlTransfer] = {}
        self._control_transfer_counts: dict[str, int] = {}

    def _ctinc(self, key: str) -> None:
        self._control_transfer_counts[key] = self._control_transfer_counts.get(key, 0) + 1

    def _interaction_midpoint(self, setup: Any) -> float:
        if not 0 <= setup.interaction_index < len(self.decision_bars):
            raise RuntimeError("interaction index is outside decision history")
        interaction = self.decision_bars[setup.interaction_index]
        return (interaction.high + interaction.low) / 2.0

    @staticmethod
    def _opposite_quote(side: Side, observation: FlowObservation) -> float:
        if side is Side.LONG:
            return max(0.0, -observation.signed_taker_quote)
        return max(0.0, observation.signed_taker_quote)

    @staticmethod
    def _aligned_quote(side: Side, observation: FlowObservation) -> float:
        if side is Side.LONG:
            return max(0.0, observation.signed_taker_quote)
        return max(0.0, -observation.signed_taker_quote)

    @staticmethod
    def _deeper(side: Side, candidate: float, existing: float) -> bool:
        return candidate < existing if side is Side.LONG else candidate > existing

    def _capture_current_absorption(
        self,
        setup: Any,
        bar: Any,
        observation: FlowObservation,
        signal: FlowSignal,
    ) -> None:
        _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
        extreme = bar.low if setup.side is Side.LONG else bar.high
        existing = self._pending_control_transfers.get(setup.setup_id)
        if existing is not None and not self._deeper(setup.side, extreme, existing.sweep_extreme):
            self._ctinc("additional_current_absorption_without_deeper_sweep")
            return
        self._pending_control_transfers[setup.setup_id] = PendingControlTransfer(
            sweep_time_ns=bar.ts_close_ns,
            sweep_extreme=extreme,
            boundary_lower=lower,
            boundary_upper=upper,
            adverse_signed_quote=self._opposite_quote(setup.side, observation),
            signal_kind=signal.kind,
            interaction_midpoint=self._interaction_midpoint(setup),
        )
        self._ctinc("current_absorption_parked_for_decision_frame")
        self._trace(
            "current_absorption_parked_for_decision_frame",
            bar.ts_close_ns,
            setup,
            sweep_extreme=extreme,
            boundary_lower=lower,
            boundary_upper=upper,
            interaction_midpoint=self._interaction_midpoint(setup),
            adverse_signed_quote=self._opposite_quote(setup.side, observation),
            decision_minutes=self.decision_minutes,
            rule_provenance=(
                DECISION_FRAME_CONTROL_TRANSFER_RULE,
                IMPACT_EFFICIENCY_TRANSFER_RULE,
            ),
        )

    def _decision_frame_closed(self, time_ns: int) -> bool:
        minute = time_ns // 60_000_000_000
        return minute % self.decision_minutes == 0

    def _pending_signal(
        self,
        setup: Any,
        bar: Any,
        observation: FlowObservation | None,
    ) -> FlowSignal | None:
        pending = self._pending_control_transfers.get(setup.setup_id)
        if pending is None or observation is None:
            return None
        if bar.ts_close_ns <= pending.sweep_time_ns:
            return None

        current_extreme = bar.low if setup.side is Side.LONG else bar.high
        if self._deeper(setup.side, current_extreme, pending.sweep_extreme):
            self._pending_control_transfers.pop(setup.setup_id, None)
            self._ctinc("pending_transfer_lost_to_new_adverse_extreme")
            self._trace(
                "pending_transfer_lost_to_new_adverse_extreme",
                bar.ts_close_ns,
                setup,
                original_sweep_extreme=pending.sweep_extreme,
                new_extreme=current_extreme,
                rule_provenance=DECISION_FRAME_CONTROL_TRANSFER_RULE,
            )
            return None
        if not self._decision_frame_closed(bar.ts_close_ns):
            self._ctinc("pending_transfer_waiting_decision_close")
            return None

        _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
        boundary_reclaimed = bar.close > upper if setup.side is Side.LONG else bar.close < lower
        balance_reclaimed = (
            bar.close >= pending.interaction_midpoint
            if setup.side is Side.LONG
            else bar.close <= pending.interaction_midpoint
        )
        if not boundary_reclaimed or not balance_reclaimed:
            self._ctinc("decision_frame_closed_without_control_reclaim")
            self._trace(
                "decision_frame_closed_without_control_reclaim",
                bar.ts_close_ns,
                setup,
                close=bar.close,
                projected_lower=lower,
                projected_upper=upper,
                interaction_midpoint=pending.interaction_midpoint,
                boundary_reclaimed=boundary_reclaimed,
                balance_reclaimed=balance_reclaimed,
                rule_provenance=DECISION_FRAME_CONTROL_TRANSFER_RULE,
            )
            return None

        episode = [
            item
            for item in self.flow_analyzer.history
            if pending.sweep_time_ns <= item.ts_close_ns <= bar.ts_close_ns
        ]
        if not episode:
            return None
        adverse_quote = sum(self._opposite_quote(setup.side, item) for item in episode)
        aligned_quote = sum(self._aligned_quote(setup.side, item) for item in episode)
        absolute_quote = sum(abs(item.signed_taker_quote) for item in episode)
        cumulative_delta = sum(item.signed_taker_quote for item in episode)
        penetration = (
            max(0.0, pending.boundary_lower - pending.sweep_extreme)
            if setup.side is Side.LONG
            else max(0.0, pending.sweep_extreme - pending.boundary_upper)
        )
        recovery = (
            bar.close - pending.sweep_extreme
            if setup.side is Side.LONG
            else pending.sweep_extreme - bar.close
        )
        if penetration <= 0.0 or recovery <= 0.0 or adverse_quote <= 0.0:
            self._ctinc("decision_frame_missing_complete_sweep_recovery")
            return None

        adverse_impact = penetration / adverse_quote
        recovery_impact = recovery / max(absolute_quote, self.tick_size)
        cumulative_remains_adverse = (
            cumulative_delta < 0.0
            if setup.side is Side.LONG
            else cumulative_delta > 0.0
        )
        intended_initiative = aligned_quote > adverse_quote and recovery_impact > adverse_impact
        passive_absorption = cumulative_remains_adverse
        if not passive_absorption and not intended_initiative:
            self._ctinc("decision_frame_reclaim_without_impact_transfer")
            self._trace(
                "decision_frame_reclaim_without_impact_transfer",
                bar.ts_close_ns,
                setup,
                adverse_quote=adverse_quote,
                aligned_quote=aligned_quote,
                cumulative_signed_taker_quote=cumulative_delta,
                penetration=penetration,
                recovery=recovery,
                adverse_impact_per_quote=adverse_impact,
                recovery_impact_per_quote=recovery_impact,
                rule_provenance=IMPACT_EFFICIENCY_TRANSFER_RULE,
            )
            return None

        mechanism = (
            "DECISION_FRAME_PASSIVE_ABSORPTION_CONTROL_TRANSFER"
            if passive_absorption
            else "DECISION_FRAME_REINITIATIVE_CONTROL_TRANSFER"
        )
        strength = recovery_impact / max(adverse_impact, 1e-18)
        self._pending_control_transfers.pop(setup.setup_id, None)
        self._ctinc("decision_frame_control_transfer_confirmed")
        self._ctinc(
            "decision_frame_passive_absorption_confirmed"
            if passive_absorption
            else "decision_frame_reinitiative_confirmed"
        )
        self._trace(
            "decision_frame_control_transfer_confirmed",
            bar.ts_close_ns,
            setup,
            mechanism=mechanism,
            sweep_time_ns=pending.sweep_time_ns,
            decision_close_time_ns=bar.ts_close_ns,
            interaction_midpoint=pending.interaction_midpoint,
            close=bar.close,
            adverse_quote=adverse_quote,
            aligned_quote=aligned_quote,
            cumulative_signed_taker_quote=cumulative_delta,
            penetration=penetration,
            recovery=recovery,
            adverse_impact_per_quote=adverse_impact,
            recovery_impact_per_quote=recovery_impact,
            impact_efficiency_ratio=strength,
            episode_bars=len(episode),
            rule_provenance=(
                DECISION_FRAME_CONTROL_TRANSFER_RULE,
                IMPACT_EFFICIENCY_TRANSFER_RULE,
            ),
        )
        return FlowSignal(
            kind=pending.signal_kind,
            mechanism=mechanism,
            strength=strength,
            observation=observation,
            episode_bars=len(episode),
            cumulative_signed_taker_quote=cumulative_delta,
            net_price_progress=recovery,
        )

    def _reversal_absorption_signal(
        self,
        setup: Any,
        bar: Any,
        observation: FlowObservation | None,
    ) -> FlowSignal | None:
        delayed = self._pending_signal(setup, bar, observation)
        if delayed is not None:
            return delayed

        signal = super()._reversal_absorption_signal(setup, bar, observation)
        if signal is None:
            return None
        if signal.mechanism != "SWEEP_RECLAIM_CURRENT_ABSORPTION":
            self._ctinc("noncurrent_absorption_kept_diagnostic_only")
            return None
        if observation is None:
            raise RuntimeError("current absorption lost its flow observation")
        self._capture_current_absorption(setup, bar, observation, signal)
        return None

    @property
    def decision_frame_control_transfer_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._control_transfer_counts.items())),
            "pending": len(self._pending_control_transfers),
            "decision_minutes": self.decision_minutes,
            "rules": (
                DECISION_FRAME_CONTROL_TRANSFER_RULE,
                IMPACT_EFFICIENCY_TRANSFER_RULE,
            ),
        }


class ControlTransferMicroEngine(
    DecisionFrameControlTransferMixin,
    FixedRejectionTargetMicroEngine,
):
    pass


class ControlTransferMajorSwingEngine(
    DecisionFrameControlTransferMixin,
    FixedRejectionTargetMajorSwingEngine,
):
    pass


class ControlTransferDecisionOBEngine(
    DecisionFrameControlTransferMixin,
    FixedRejectionTargetDecisionOBEngine,
):
    pass


class ControlTransferDirectSweepEngine(
    DecisionFrameControlTransferMixin,
    FixedRejectionTargetDirectSweepEngine,
):
    pass


class EasyChartRE1ControlTransferBundle(EasyChartRE1RejectionMicroTargetV2Bundle):
    """Rejection-only visual core with delayed flow-only control transfer."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = ControlTransferMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = ControlTransferMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = ControlTransferDecisionOBEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.direct_sweep_ob = ControlTransferDirectSweepEngine(
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
        output["decision_frame_control_transfer"] = {
            "micro": self.micro.decision_frame_control_transfer_diagnostics,
            "major_swing": self.major_swing.decision_frame_control_transfer_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.decision_frame_control_transfer_diagnostics,
            "direct_sweep_ob": self.direct_sweep_ob.decision_frame_control_transfer_diagnostics,
            "rules": (
                DECISION_FRAME_CONTROL_TRANSFER_RULE,
                IMPACT_EFFICIENCY_TRANSFER_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ControlTransferBundle
