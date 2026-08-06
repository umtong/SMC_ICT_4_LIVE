"""Candidate 09 v13: v10 plus failed-boundary invalidation salvage.

Every immediately tradeable v10 reversal remains unchanged in baseline. Only a
confirmed accepted-breakout failure rejected by v10's accepted-excursion stop is
re-evaluated with invalidation beyond the failed boundary and failure bar. This
matches the scenario thesis: once the accepted auction has failed, renewed
acceptance beyond that boundary invalidates the reversal. Full entry/stop/exit
costs remain inside both net reward-to-risk and 3% NAV sizing. The old separate
price-risk-versus-cost floor is retained only as a controlled ablation.
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
    enable_boundary_stop_salvage: bool = True
    boundary_stop_all_reversals: bool = False
    enforce_price_risk_floor: bool = False

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {
            "baseline",
            "no-boundary-stop-salvage",
            "with-price-risk-floor",
            "boundary-stop-all",
        }
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")

        mapped = deepcopy(dict(payload))
        mapped["structure"] = dict(payload["structure"])
        mapped["structure"]["auction_horizons_minutes"] = [15, 60, 1440]
        base = V10EngineConfig.from_mapping(mapped, ablation="baseline")
        inherited = {field.name: getattr(base, field.name) for field in fields(V10EngineConfig)}
        return cls(
            **inherited,
            enable_boundary_stop_salvage=ablation != "no-boundary-stop-salvage",
            boundary_stop_all_reversals=ablation == "boundary-stop-all",
            enforce_price_risk_floor=ablation == "with-price-risk-floor",
        )


class LiquidityStateEngine(V10LiquidityStateEngine):
    config: EngineConfig

    def __init__(self, config: EngineConfig):
        super().__init__(config)
        self._boundary_attempted = False
        self._boundary_diagnostic: dict[str, Any] = {}

    def _build_signal(self, pending: PendingResolution, bar: FlowBar, *, branch: str) -> Signal | None:
        if branch != "REVERSAL":
            return super()._build_signal(pending, bar, branch=branch)

        immediate = super()._build_signal(pending, bar, branch=branch)
        should_attempt = self.config.boundary_stop_all_reversals or (
            immediate is None and self.config.enable_boundary_stop_salvage
        )
        if not should_attempt:
            return immediate

        self._boundary_attempted = True
        signal, diagnostic = self._build_boundary_stop_signal(pending, bar)
        self._boundary_diagnostic = diagnostic
        if immediate is not None:
            self._boundary_diagnostic.update(
                {
                    "accepted_extreme_stop_net_reward_to_risk": immediate.net_reward_to_risk,
                    "accepted_extreme_stop": immediate.stop_price,
                    "boundary_stop_all_controlled_ablation": True,
                },
            )
        return signal

    def _finish(
        self,
        pending: PendingResolution,
        bar: FlowBar,
        signal: Signal | None,
        events: list[DiagnosticEvent],
    ) -> Signal | None:
        if self._boundary_attempted:
            events.append(
                self._event(
                    pending,
                    bar,
                    "BOUNDARY_STOP_SALVAGE_APPROVED" if signal else "BOUNDARY_STOP_SALVAGE_REJECTED",
                    pending.state,
                    "ENTERABLE" if signal else "NO_TRADE",
                    signal.reason_code
                    if signal
                    else str(self._boundary_diagnostic.get("rejection_reason", "BOUNDARY_STOP_UNTRADEABLE")),
                    self._boundary_diagnostic,
                ),
            )
        self._boundary_attempted = False
        self._boundary_diagnostic = {}
        return super()._finish(pending, bar, signal, events)

    def _build_boundary_stop_signal(
        self,
        pending: PendingResolution,
        bar: FlowBar,
    ) -> tuple[Signal | None, dict[str, Any]]:
        entry = bar.close
        atr = max(self._atr, 1e-12)
        level = pending.level

        if pending.direction == "UP":
            side = "SELL"
            stop = max(level.price, bar.high) + self.config.stop_buffer_atr * atr
            target = level.range_midpoint if level.range_midpoint < entry else level.range_low
            geometry_ok = target < entry < stop
        else:
            side = "BUY"
            stop = min(level.price, bar.low) - self.config.stop_buffer_atr * atr
            target = level.range_midpoint if level.range_midpoint > entry else level.range_high
            geometry_ok = stop < entry < target

        cost = self.config.composite_cost_per_fill
        price_risk = abs(entry - stop)
        net_risk = price_risk + cost * entry + cost * stop
        net_reward = abs(target - entry) - cost * entry - cost * target
        net_rr = net_reward / net_risk if geometry_ok and net_risk > 0.0 and net_reward > 0.0 else None

        diagnostic: dict[str, Any] = {
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "geometry_ok": geometry_ok,
            "price_risk": price_risk,
            "round_trip_cost_floor": 2.0 * cost * entry,
            "net_risk_per_unit": net_risk,
            "net_reward_per_unit": net_reward,
            "net_reward_to_risk": net_rr,
            "minimum_net_reward_to_risk": self.config.minimum_net_reward_to_risk,
            "enforce_price_risk_floor": self.config.enforce_price_risk_floor,
            "failed_level_price": level.price,
            "failed_level_horizon_minutes": level.horizon_minutes,
            "accepted_extreme": pending.extreme,
            "failure_bar_high": bar.high,
            "failure_bar_low": bar.low,
            "stop_model": "FAILED_BOUNDARY_AND_FAILURE_BAR",
        }

        rejection: str | None = None
        if not geometry_ok:
            rejection = "BOUNDARY_STOP_HAS_INVALID_TARGET_GEOMETRY"
        elif self.config.enforce_price_risk_floor and price_risk < 2.0 * cost * entry:
            rejection = "BOUNDARY_STOP_PRICE_RISK_BELOW_REDUNDANT_COST_FLOOR"
        elif net_risk <= 0.0 or net_reward <= 0.0:
            rejection = "BOUNDARY_STOP_HAS_NONPOSITIVE_REWARD_AFTER_COST"
        elif net_rr is None or net_rr < self.config.minimum_net_reward_to_risk:
            rejection = "BOUNDARY_STOP_NET_REWARD_TO_RISK_BELOW_GATE"

        if rejection is not None:
            diagnostic["rejection_reason"] = rejection
            return None, diagnostic

        reason = "ACCEPTED_BREAKOUT_FAILURE_WITH_BOUNDARY_REACCEPTANCE_INVALIDATION"
        return (
            Signal(
                scenario_id=pending.scenario_id,
                branch="REVERSAL",
                side=side,
                observed_time_ns=bar.ts_ns,
                entry_reference=entry,
                stop_price=stop,
                target_price=target,
                net_reward_to_risk=float(net_rr),
                reason_code=reason,
                details={**diagnostic, "entry_order_type": "MARKET"},
            ),
            diagnostic,
        )


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
