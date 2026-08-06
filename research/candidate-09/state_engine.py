"""Candidate 09 v12: v10 reversal plus passive failed-boundary limit salvage.

Every immediately tradeable v10 market reversal remains unchanged in baseline.
When the accepted-breakout failure is logically complete but the failure-close
entry is rejected by the unchanged cost/target/RR geometry, v12 may rest a GTC
limit bracket at the already observed failed boundary. The stop remains beyond
the original accepted excursion and the target remains the original v4 source-
range equilibrium. The order itself is the retest: no future bar is used to move
the entry, stop, or target.
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
    enable_limit_salvage: bool = True
    limit_all_reversals: bool = False
    limit_entry_timeout_bars: int = 12

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {"baseline", "no-limit-salvage", "limit-all", "no-flow"}
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        mapped = deepcopy(dict(payload))
        mapped["structure"] = dict(payload["structure"])
        mapped["structure"]["auction_horizons_minutes"] = [15, 60, 1440]
        base = V10EngineConfig.from_mapping(mapped, ablation="no-flow" if ablation == "no-flow" else "baseline")
        inherited = {field.name: getattr(base, field.name) for field in fields(V10EngineConfig)}
        timeout = int(payload["trade"].get("limit_entry_timeout_bars", 12))
        if timeout <= 0:
            raise ValueError("limit_entry_timeout_bars must be positive")
        return cls(
            **inherited,
            enable_limit_salvage=ablation != "no-limit-salvage",
            limit_all_reversals=ablation == "limit-all",
            limit_entry_timeout_bars=timeout,
        )


class LiquidityStateEngine(V10LiquidityStateEngine):
    config: EngineConfig

    def __init__(self, config: EngineConfig):
        super().__init__(config)
        self._limit_attempted = False
        self._limit_diagnostic: dict[str, Any] = {}

    def _build_signal(self, pending: PendingResolution, bar: FlowBar, *, branch: str) -> Signal | None:
        if branch != "REVERSAL":
            return super()._build_signal(pending, bar, branch=branch)

        immediate = super()._build_signal(pending, bar, branch=branch)
        should_limit = self.config.limit_all_reversals or (
            immediate is None and self.config.enable_limit_salvage
        )
        if not should_limit:
            return immediate

        self._limit_attempted = True
        signal, diagnostic = self._build_failed_boundary_limit(pending, bar)
        self._limit_diagnostic = diagnostic
        if immediate is not None:
            self._limit_diagnostic.update({
                "immediate_net_reward_to_risk": immediate.net_reward_to_risk,
                "immediate_side": immediate.side,
                "immediate_stop": immediate.stop_price,
                "immediate_target": immediate.target_price,
                "limit_all_controlled_ablation": True,
            })
        return signal

    def _finish(
        self,
        pending: PendingResolution,
        bar: FlowBar,
        signal: Signal | None,
        events: list[DiagnosticEvent],
    ) -> Signal | None:
        if self._limit_attempted:
            event_type = "FAILED_BOUNDARY_LIMIT_APPROVED" if signal is not None else "FAILED_BOUNDARY_LIMIT_REJECTED"
            next_state = "ENTRY_PENDING" if signal is not None else "NO_TRADE"
            reason = signal.reason_code if signal is not None else str(
                self._limit_diagnostic.get("rejection_reason", "LIMIT_GEOMETRY_UNTRADEABLE")
            )
            events.append(self._event(
                pending,
                bar,
                event_type,
                pending.state,
                next_state,
                reason,
                self._limit_diagnostic,
            ))
        self._limit_attempted = False
        self._limit_diagnostic = {}
        return super()._finish(pending, bar, signal, events)

    def _build_failed_boundary_limit(
        self,
        pending: PendingResolution,
        bar: FlowBar,
    ) -> tuple[Signal | None, dict[str, Any]]:
        atr = max(self._atr, 1e-12)
        level = pending.level
        entry = level.price
        if pending.direction == "UP":
            side = "SELL"
            stop = max(pending.extreme, bar.high) + self.config.stop_buffer_atr * atr
            target = level.range_midpoint if level.range_midpoint < entry else level.range_low
            geometry_ok = target < entry < stop
            passive_ok = entry > bar.close
        else:
            side = "BUY"
            stop = min(pending.extreme, bar.low) - self.config.stop_buffer_atr * atr
            target = level.range_midpoint if level.range_midpoint > entry else level.range_high
            geometry_ok = stop < entry < target
            passive_ok = entry < bar.close

        cost = self.config.composite_cost_per_fill
        price_risk = abs(entry - stop)
        net_risk = price_risk + cost * entry + cost * stop
        net_reward = abs(target - entry) - cost * entry - cost * target
        net_rr = net_reward / net_risk if geometry_ok and net_risk > 0.0 and net_reward > 0.0 else None
        diagnostic: dict[str, Any] = {
            "side": side,
            "failure_close": bar.close,
            "limit_entry": entry,
            "stop": stop,
            "target": target,
            "geometry_ok": geometry_ok,
            "passive_limit_ok": passive_ok,
            "price_risk": price_risk,
            "round_trip_cost_floor": 2.0 * cost * entry,
            "net_risk_per_unit": net_risk,
            "net_reward_per_unit": net_reward,
            "net_reward_to_risk": net_rr,
            "minimum_net_reward_to_risk": self.config.minimum_net_reward_to_risk,
            "level_price": level.price,
            "horizon_minutes": level.horizon_minutes,
            "accepted_extreme": pending.extreme,
            "entry_order_type": "LIMIT",
            "entry_timeout_bars": self.config.limit_entry_timeout_bars,
            "entry_model": "FAILED_BOUNDARY_RETEST_LIMIT",
        }
        rejection: str | None = None
        if not passive_ok:
            rejection = "FAILED_BOUNDARY_LIMIT_WOULD_BE_MARKETABLE_AT_DECISION_TIME"
        elif not geometry_ok:
            rejection = "FAILED_BOUNDARY_LIMIT_HAS_INVALID_STOP_TARGET_GEOMETRY"
        elif price_risk < 2.0 * cost * entry:
            rejection = "FAILED_BOUNDARY_LIMIT_PRICE_RISK_TOO_SMALL_RELATIVE_TO_COST"
        elif net_risk <= 0.0 or net_reward <= 0.0:
            rejection = "FAILED_BOUNDARY_LIMIT_HAS_NONPOSITIVE_REWARD_AFTER_COST"
        elif net_rr is None or net_rr < self.config.minimum_net_reward_to_risk:
            rejection = "FAILED_BOUNDARY_LIMIT_NET_REWARD_TO_RISK_BELOW_GATE"
        if rejection is not None:
            diagnostic["rejection_reason"] = rejection
            return None, diagnostic

        reason = "ACCEPTED_BREAKOUT_FAILURE_LIMIT_RETEST_TO_EQUILIBRIUM"
        return Signal(
            scenario_id=pending.scenario_id,
            branch="REVERSAL",
            side=side,
            observed_time_ns=bar.ts_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            net_reward_to_risk=float(net_rr),
            reason_code=reason,
            details={
                **diagnostic,
                "entry_order_type": "LIMIT",
                "entry_timeout_bars": self.config.limit_entry_timeout_bars,
                "limit_submitted_after_failure_ns": bar.ts_ns,
            },
        ), diagnostic


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
