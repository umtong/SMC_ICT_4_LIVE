"""Candidate 09 v11: v10 reversal plus targeted failed-level retest salvage.

The positive v10 baseline is preserved exactly for every immediately tradeable
accepted-breakout reversal. Only a logically confirmed reversal that is rejected
by the unchanged v4 cost/target/RR geometry is staged for the first causal retest
of the failed boundary. The retest must reject back inside with reversal-direction
body and flow. Entry is recalculated there while the original accepted excursion
remains the stop anchor and the original v4 equilibrium target remains unchanged.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields
from typing import Any, Mapping

from state_engine_v10_direct import (
    MINUTE_NS,
    AuctionLevel,
    DiagnosticEvent,
    EngineConfig as V10EngineConfig,
    EngineResult,
    FlowBar,
    LiquidityStateEngine as V10LiquidityStateEngine,
    PendingResolution,
    RiskSizing,
    Signal,
    risk_based_quantity,
)


@dataclass(frozen=True, slots=True)
class EngineConfig(V10EngineConfig):
    enable_retest_salvage: bool = True
    retest_all_reversals: bool = False

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {"baseline", "no-retest-salvage", "retest-all", "no-flow"}
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        mapped = deepcopy(dict(payload))
        mapped["structure"] = dict(payload["structure"])
        mapped["structure"]["auction_horizons_minutes"] = [15, 60, 1440]
        base = V10EngineConfig.from_mapping(mapped, ablation="no-flow" if ablation == "no-flow" else "baseline")
        inherited = {field.name: getattr(base, field.name) for field in fields(V10EngineConfig)}
        return cls(
            **inherited,
            enable_retest_salvage=ablation != "no-retest-salvage",
            retest_all_reversals=ablation == "retest-all",
        )


class LiquidityStateEngine(V10LiquidityStateEngine):
    config: EngineConfig

    def __init__(self, config: EngineConfig):
        super().__init__(config)
        self._retest_staged = False
        self._stage_reason = ""
        self._stage_diagnostic: dict[str, Any] = {}

    def _build_signal(self, pending: PendingResolution, bar: FlowBar, *, branch: str) -> Signal | None:
        if branch != "REVERSAL":
            return super()._build_signal(pending, bar, branch=branch)

        immediate = super()._build_signal(pending, bar, branch=branch)
        should_stage = self.config.retest_all_reversals or (
            immediate is None and self.config.enable_retest_salvage
        )
        if not should_stage:
            return immediate

        pending.state = "FAILED_WAIT_RETEST_ALL" if immediate is not None else "FAILED_UNTRADEABLE_WAIT_RETEST"
        pending.retest_index = self._index
        pending.retest_high = bar.high
        pending.retest_low = bar.low
        self._retest_staged = True
        self._stage_reason = (
            "CONTROLLED_ABLATION_REQUIRES_RETEST_FOR_ALL_REVERSALS"
            if immediate is not None
            else "IMMEDIATE_FAILURE_ENTRY_UNTRADEABLE_STAGE_FIRST_RETEST"
        )
        self._stage_diagnostic = self._reversal_diagnostic(pending, bar)
        if immediate is not None:
            self._stage_diagnostic.update({
                "immediate_net_reward_to_risk": immediate.net_reward_to_risk,
                "immediate_side": immediate.side,
                "immediate_stop": immediate.stop_price,
                "immediate_target": immediate.target_price,
            })
        return None

    def _finish(
        self,
        pending: PendingResolution,
        bar: FlowBar,
        signal: Signal | None,
        events: list[DiagnosticEvent],
    ) -> Signal | None:
        if signal is None and self._retest_staged:
            events.append(self._event(
                pending,
                bar,
                "REVERSAL_STAGED_FOR_FAILURE_RETEST",
                "ACCEPTED_OR_RETESTED",
                pending.state,
                self._stage_reason,
                self._stage_diagnostic,
            ))
            self._retest_staged = False
            self._stage_reason = ""
            self._stage_diagnostic = {}
            return None
        self._retest_staged = False
        self._stage_reason = ""
        self._stage_diagnostic = {}
        return super()._finish(pending, bar, signal, events)

    def _advance_pending(self, bar: FlowBar, events: list[DiagnosticEvent]) -> Signal | None:
        pending = self._pending
        assert pending is not None
        if pending.state not in {"FAILED_UNTRADEABLE_WAIT_RETEST", "FAILED_WAIT_RETEST_ALL"}:
            return super()._advance_pending(bar, events)

        assert pending.retest_index is not None
        if self._failure_reaccepted(pending, bar):
            self._expire(pending, bar, "FAILED_BOUNDARY_REACCEPTED_BEFORE_REVERSAL_ENTRY", events)
            return None

        if self._index > pending.retest_index and self._failure_retest_rejected(pending, bar):
            events.append(self._event(
                pending,
                bar,
                "FAILED_BOUNDARY_RETEST_REJECTED",
                pending.state,
                "RETEST_REJECTED",
                "FIRST_FAILED_LEVEL_RETEST_REJECTED_BACK_INSIDE_AUCTION",
                self._reversal_diagnostic(pending, bar),
            ))
            pending.state = "RETEST_REJECTED"
            signal = super()._build_signal(pending, bar, branch="REVERSAL")
            if signal is None:
                events.append(self._event(
                    pending,
                    bar,
                    "SCENARIO_REJECTED",
                    "RETEST_REJECTED",
                    "NO_TRADE",
                    "FAILURE_RETEST_STILL_UNTRADEABLE_UNDER_ORIGINAL_TARGET_AND_STOP",
                    self._reversal_diagnostic(pending, bar),
                ))
                self._pending = None
                self._cooldown = self.config.cooldown_bars
                return None
            return self._finish(pending, bar, signal, events)

        if self._index - pending.retest_index > self.config.retest_timeout_bars:
            self._expire(pending, bar, "FAILED_BOUNDARY_DID_NOT_RETEST_AND_REJECT_WITHIN_WINDOW", events)
        return None

    def _failure_reaccepted(self, pending: PendingResolution, bar: FlowBar) -> bool:
        buffer = self.config.acceptance_buffer_atr * self._atr
        if pending.direction == "UP":
            return bar.close >= pending.level.price + buffer
        return bar.close <= pending.level.price - buffer

    def _failure_retest_rejected(self, pending: PendingResolution, bar: FlowBar) -> bool:
        tolerance = self.config.retest_tolerance_atr * self._atr
        inside_buffer = self.config.failure_close_buffer_atr * self._atr
        body = abs(bar.close - bar.open) / max(self._atr, 1e-12)
        if pending.direction == "UP":
            touched = bar.high >= pending.level.price - tolerance
            rejected = bar.close <= pending.level.price - inside_buffer and bar.close < bar.open
            flow_ok = bar.flow_imbalance <= -self.config.directional_imbalance
        else:
            touched = bar.low <= pending.level.price + tolerance
            rejected = bar.close >= pending.level.price + inside_buffer and bar.close > bar.open
            flow_ok = bar.flow_imbalance >= self.config.directional_imbalance
        if not self.config.use_flow_confirmation:
            flow_ok = True
        return touched and rejected and body >= self.config.minimum_resolution_displacement_atr and flow_ok

    def _reversal_diagnostic(self, pending: PendingResolution, bar: FlowBar) -> dict[str, Any]:
        entry = bar.close
        atr = max(self._atr, 1e-12)
        level = pending.level
        if pending.direction == "UP":
            side = "SELL"
            stop = max(pending.extreme, bar.high) + self.config.stop_buffer_atr * atr
            target = level.range_midpoint if level.range_midpoint < entry else level.range_low
            geometry_ok = target < entry < stop
        else:
            side = "BUY"
            stop = min(pending.extreme, bar.low) - self.config.stop_buffer_atr * atr
            target = level.range_midpoint if level.range_midpoint > entry else level.range_high
            geometry_ok = stop < entry < target
        cost = self.config.composite_cost_per_fill
        price_risk = abs(entry - stop)
        net_risk = price_risk + cost * entry + cost * stop
        net_reward = abs(target - entry) - cost * entry - cost * target
        net_rr = net_reward / net_risk if geometry_ok and net_risk > 0.0 and net_reward > 0.0 else None
        return {
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "geometry_ok": geometry_ok,
            "price_risk": price_risk,
            "net_risk_per_unit": net_risk,
            "net_reward_per_unit": net_reward,
            "net_reward_to_risk": net_rr,
            "minimum_net_reward_to_risk": self.config.minimum_net_reward_to_risk,
            "level_price": level.price,
            "horizon_minutes": level.horizon_minutes,
            "accepted_extreme": pending.extreme,
            "entry_model": "FAILURE_RETEST" if pending.state == "RETEST_REJECTED" else "FAILURE_CLOSE",
        }


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
