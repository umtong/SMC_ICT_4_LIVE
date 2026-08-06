"""Candidate 09 v15: classify failed acceptance by price impact relative to order flow.

The detector, accepted-breakout failure, entry time, failed-boundary invalidation,
equilibrium target, cost model, and 3% NAV loss budget are inherited unchanged from
v14. This layer only distinguishes two causal resolutions of an accepted auction:

* PASSIVE_ABSORPTION: price re-enters the old range even though cumulative aggressive
  flow since the breach remains aligned with the failed breakout;
* ACTIVE_LIQUIDITY_FLIP: cumulative flow has reversed and the failure bar moves price
  through the boundary at least as efficiently per unit of opposite flow as the
  original acceptance moved price outside per unit of aligned flow.

The comparison is dimensionless and event-relative. It has no fitted threshold other
than equality (impact ratio >= 1). The v14 unclassified behavior and each mechanism
alone are retained as controlled ablations. NautilusTrader remains the sole execution
and accounting engine.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping

from state_engine_v14_direct import (
    MINUTE_NS,
    AuctionLevel,
    DiagnosticEvent,
    EngineConfig as V14EngineConfig,
    EngineResult,
    FlowBar,
    LiquidityStateEngine as V14LiquidityStateEngine,
    PendingResolution,
    RiskSizing,
    Signal,
    risk_based_quantity,
)


@dataclass(frozen=True, slots=True)
class EngineConfig(V14EngineConfig):
    impact_resolution_mode: str = "causal"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {
            "baseline",
            "no-impact-classification",
            "passive-absorption-only",
            "active-impact-flip-only",
        }
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")

        base = V14EngineConfig.from_mapping(payload, ablation="baseline")
        inherited = {field.name: getattr(base, field.name) for field in fields(V14EngineConfig)}
        mode = {
            "baseline": "causal",
            "no-impact-classification": "all",
            "passive-absorption-only": "passive-only",
            "active-impact-flip-only": "active-only",
        }[ablation]
        return cls(**inherited, impact_resolution_mode=mode)


class LiquidityStateEngine(V14LiquidityStateEngine):
    config: EngineConfig

    def __init__(self, config: EngineConfig):
        super().__init__(config)
        self._acceptance_snapshots: dict[str, dict[str, float]] = {}
        self._impact_diagnostic: dict[str, Any] = {}

    def _event(
        self,
        pending: PendingResolution,
        bar: FlowBar,
        event_type: str,
        previous_state: str,
        next_state: str,
        reason_code: str,
        extra: Mapping[str, Any] | None = None,
    ) -> DiagnosticEvent:
        if event_type == "OUTSIDE_ACCEPTANCE":
            sign = 1.0 if pending.direction == "UP" else -1.0
            atr = max(self._atr, 1e-12)
            outside_distance_atr = sign * (bar.close - pending.level.price) / atr
            aligned_flow = sign * pending.post_flow_imbalance
            if outside_distance_atr <= 0.0 or aligned_flow <= 0.0:
                raise AssertionError("accepted auction lacks aligned price/flow state")
            self._acceptance_snapshots[pending.scenario_id] = {
                "acceptance_close": bar.close,
                "acceptance_atr": atr,
                "acceptance_outside_distance_atr": outside_distance_atr,
                "acceptance_aligned_flow": aligned_flow,
                "acceptance_impact_efficiency": outside_distance_atr / aligned_flow,
                "acceptance_observed_ns": float(bar.ts_ns),
            }
        return super()._event(
            pending,
            bar,
            event_type,
            previous_state,
            next_state,
            reason_code,
            extra,
        )

    def _failure_confirmed(self, pending: PendingResolution, bar: FlowBar) -> bool:
        confirmed = super()._failure_confirmed(pending, bar)
        if not confirmed:
            return False

        snapshot = self._acceptance_snapshots.get(pending.scenario_id)
        if snapshot is None:
            raise AssertionError("confirmed failure has no causal acceptance snapshot")

        sign = 1.0 if pending.direction == "UP" else -1.0
        atr = max(self._atr, 1e-12)
        failure_inside_distance_atr = -sign * (bar.close - pending.level.price) / atr
        opposite_failure_flow = -sign * bar.flow_imbalance
        if failure_inside_distance_atr <= 0.0 or opposite_failure_flow <= 0.0:
            raise AssertionError("confirmed failure lacks opposite price/flow response")

        failure_impact_efficiency = failure_inside_distance_atr / opposite_failure_flow
        acceptance_impact_efficiency = snapshot["acceptance_impact_efficiency"]
        impact_ratio = failure_impact_efficiency / max(acceptance_impact_efficiency, 1e-12)
        residual_aligned_flow = sign * pending.post_flow_imbalance
        passive_absorption = residual_aligned_flow > 0.0
        active_liquidity_flip = residual_aligned_flow <= 0.0 and impact_ratio >= 1.0

        if passive_absorption:
            resolution_class = "PASSIVE_ABSORPTION"
        elif active_liquidity_flip:
            resolution_class = "ACTIVE_LIQUIDITY_FLIP"
        else:
            resolution_class = "UNCONFIRMED_FLOW_REVERSAL"

        self._impact_diagnostic = {
            **snapshot,
            "failure_bar_flow_imbalance": bar.flow_imbalance,
            "failure_opposite_flow": opposite_failure_flow,
            "failure_inside_distance_atr": failure_inside_distance_atr,
            "failure_impact_efficiency": failure_impact_efficiency,
            "impact_efficiency_ratio": impact_ratio,
            "cumulative_residual_aligned_flow": residual_aligned_flow,
            "passive_absorption": passive_absorption,
            "active_liquidity_flip": active_liquidity_flip,
            "resolution_class": resolution_class,
            "impact_resolution_mode": self.config.impact_resolution_mode,
        }
        return True

    def _classification_allowed(self) -> bool:
        mode = self.config.impact_resolution_mode
        passive = bool(self._impact_diagnostic.get("passive_absorption"))
        active = bool(self._impact_diagnostic.get("active_liquidity_flip"))
        if mode == "all":
            return True
        if mode == "causal":
            return passive or active
        if mode == "passive-only":
            return passive
        if mode == "active-only":
            return active
        raise AssertionError(f"unsupported impact resolution mode: {mode}")

    def _build_signal(self, pending: PendingResolution, bar: FlowBar, *, branch: str) -> Signal | None:
        signal = super()._build_signal(pending, bar, branch=branch)
        if branch != "REVERSAL" or not self._impact_diagnostic:
            return signal

        allowed = self._classification_allowed()
        if hasattr(self, "_boundary_diagnostic"):
            self._boundary_diagnostic.update(self._impact_diagnostic)
            self._boundary_diagnostic["impact_classification_allowed"] = allowed

        if signal is None:
            return None
        if not allowed:
            if hasattr(self, "_boundary_diagnostic"):
                self._boundary_diagnostic["rejection_reason"] = "FAILED_AUCTION_IMPACT_CLASS_NOT_CONFIRMED"
            return None

        resolution_class = str(self._impact_diagnostic["resolution_class"])
        reason = (
            signal.reason_code
            if self.config.impact_resolution_mode == "all"
            else f"ACCEPTED_BREAKOUT_FAILURE_{resolution_class}"
        )
        return Signal(
            scenario_id=signal.scenario_id,
            branch=signal.branch,
            side=signal.side,
            observed_time_ns=signal.observed_time_ns,
            entry_reference=signal.entry_reference,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            net_reward_to_risk=signal.net_reward_to_risk,
            reason_code=reason,
            details={
                **dict(signal.details),
                **self._impact_diagnostic,
                "base_reason_code": signal.reason_code,
            },
        )

    def _finish(
        self,
        pending: PendingResolution,
        bar: FlowBar,
        signal: Signal | None,
        events: list[DiagnosticEvent],
    ) -> Signal | None:
        if self._impact_diagnostic:
            events.append(
                self._event(
                    pending,
                    bar,
                    "FAILED_AUCTION_MICROSTRUCTURE_CLASSIFIED",
                    pending.state,
                    "ENTERABLE" if signal else "NO_TRADE",
                    str(self._impact_diagnostic["resolution_class"]),
                    {
                        **self._impact_diagnostic,
                        "impact_classification_allowed": self._classification_allowed(),
                    },
                ),
            )
        try:
            return super()._finish(pending, bar, signal, events)
        finally:
            self._acceptance_snapshots.pop(pending.scenario_id, None)
            self._impact_diagnostic = {}

    def _expire(
        self,
        pending: PendingResolution,
        bar: FlowBar,
        reason: str,
        events: list[DiagnosticEvent],
    ) -> None:
        self._acceptance_snapshots.pop(pending.scenario_id, None)
        self._impact_diagnostic = {}
        super()._expire(pending, bar, reason, events)


__all__ = [
    "MINUTE_NS",
    "AuctionLevel",
    "DiagnosticEvent",
    "EngineConfig",
    "EngineResult",
    "FlowBar",
    "LiquidityStateEngine",
    "PendingResolution",
    "RiskSizing",
    "Signal",
    "risk_based_quantity",
]
