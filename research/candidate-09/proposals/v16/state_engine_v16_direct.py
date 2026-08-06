"""Candidate 09 v16: unaccepted liquidity-sweep absorption as an independent scenario.

v14 remains unchanged for accepted-breakout failures.  This module adds one disjoint
scenario for a breach which fails to obtain a second outside close and immediately
returns inside the source auction:

* the observed sweep excursion is meaningful under the existing excursion contract;
* participation reaches the existing volume-ratio contract;
* the first post-breach bar closes back through the level with the existing reversal
  displacement contract;
* cumulative aggressive flow across breach and rejection remains aligned with the
  failed breakout, so price/flow disagreement identifies passive absorption;
* entry at the completed rejection close, invalidation beyond the observed sweep,
  and the source-range equilibrium target pass the unchanged full-cost 1.2R gate.

No return-fitted parameter is introduced.  ``no-unaccepted-absorption`` is the exact
v14 control.  ``no-residual-flow`` and ``no-volume-participation`` remove one causal
component each.  NautilusTrader remains the sole execution and accounting engine.
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
    enable_unaccepted_absorption: bool = True
    require_residual_aligned_flow: bool = True
    require_sweep_volume_participation: bool = True

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {
            "baseline",
            "no-unaccepted-absorption",
            "no-residual-flow",
            "no-volume-participation",
        }
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        base = V14EngineConfig.from_mapping(payload, ablation="baseline")
        inherited = {field.name: getattr(base, field.name) for field in fields(V14EngineConfig)}
        return cls(
            **inherited,
            enable_unaccepted_absorption=ablation != "no-unaccepted-absorption",
            require_residual_aligned_flow=ablation != "no-residual-flow",
            require_sweep_volume_participation=ablation != "no-volume-participation",
        )


class LiquidityStateEngine(V14LiquidityStateEngine):
    config: EngineConfig

    def _advance_pending(self, bar: FlowBar, events: list[DiagnosticEvent]) -> Signal | None:
        pending = self._pending
        assert pending is not None
        age = self._index - pending.start_index

        # Only the first completed observation after the breach is a clean one-bar
        # liquidity sweep.  All later paths remain exact v14 acceptance handling.
        if (
            self.config.enable_unaccepted_absorption
            and pending.state == "BREACHED"
            and age == 1
            and not self._outside(bar, pending)
        ):
            pending.extreme = (
                max(pending.extreme, bar.high)
                if pending.direction == "UP"
                else min(pending.extreme, bar.low)
            )
            self._accumulate(pending, bar)
            return self._resolve_unaccepted_absorption(pending, bar, events)

        return super()._advance_pending(bar, events)

    def _resolve_unaccepted_absorption(
        self,
        pending: PendingResolution,
        bar: FlowBar,
        events: list[DiagnosticEvent],
    ) -> Signal | None:
        atr = max(self._atr, 1e-12)
        level = pending.level
        body_atr = abs(bar.close - bar.open) / atr
        excursion_atr = abs(pending.extreme - level.price) / atr
        residual_aligned_flow = (
            pending.post_flow_imbalance
            if pending.direction == "UP"
            else -pending.post_flow_imbalance
        )
        close_buffer = self.config.failure_close_buffer_atr * atr
        if pending.direction == "UP":
            rejection_displacement = (
                bar.close <= level.price - close_buffer
                and bar.close < bar.open
            )
        else:
            rejection_displacement = (
                bar.close >= level.price + close_buffer
                and bar.close > bar.open
            )

        residual_ok = residual_aligned_flow > 0.0 or not self.config.require_residual_aligned_flow
        volume_ok = (
            pending.max_volume_ratio >= self.config.minimum_volume_ratio
            or not self.config.require_sweep_volume_participation
        )
        structure_ok = (
            rejection_displacement
            and body_atr >= self.config.minimum_resolution_displacement_atr
            and excursion_atr >= self.config.minimum_excursion_atr
            and residual_ok
            and volume_ok
        )
        diagnostic: dict[str, Any] = {
            "age_bars": 1,
            "direction": pending.direction,
            "failed_level_price": level.price,
            "failed_level_horizon_minutes": level.horizon_minutes,
            "observed_sweep_extreme": pending.extreme,
            "sweep_excursion_atr": excursion_atr,
            "rejection_body_atr": body_atr,
            "cumulative_post_flow": pending.post_flow_imbalance,
            "residual_aligned_flow": residual_aligned_flow,
            "max_volume_ratio": pending.max_volume_ratio,
            "rejection_displacement": rejection_displacement,
            "residual_flow_required": self.config.require_residual_aligned_flow,
            "volume_participation_required": self.config.require_sweep_volume_participation,
            "structure_confirmed": structure_ok,
            "scenario_family": "UNACCEPTED_LIQUIDITY_SWEEP_ABSORPTION",
        }

        if not structure_ok:
            if not rejection_displacement or body_atr < self.config.minimum_resolution_displacement_atr:
                reason = "IMMEDIATE_REENTRY_LACKED_OPPOSITE_DISPLACEMENT"
            elif excursion_atr < self.config.minimum_excursion_atr:
                reason = "SWEEP_EXCURSION_BELOW_EXISTING_CONTRACT"
            elif not residual_ok:
                reason = "PRICE_REENTRY_NOT_AGAINST_RESIDUAL_BREAKOUT_FLOW"
            else:
                reason = "SWEEP_LACKED_EXISTING_VOLUME_PARTICIPATION"
            events.append(self._event(
                pending,
                bar,
                "UNACCEPTED_SWEEP_ABSORPTION_REJECTED",
                "BREACHED",
                "NO_TRADE",
                reason,
                diagnostic,
            ))
            self._expire(pending, bar, "BREACH_REENTERED_RANGE_BEFORE_ACCEPTANCE", events)
            return None

        signal, trade_diagnostic = self._build_unaccepted_absorption_signal(pending, bar)
        diagnostic.update(trade_diagnostic)
        events.append(self._event(
            pending,
            bar,
            "UNACCEPTED_SWEEP_ABSORPTION_CONFIRMED",
            "BREACHED",
            "ENTERABLE" if signal else "NO_TRADE",
            signal.reason_code if signal else str(trade_diagnostic["rejection_reason"]),
            diagnostic,
        ))
        return self._finish(pending, bar, signal, events)

    def _build_unaccepted_absorption_signal(
        self,
        pending: PendingResolution,
        bar: FlowBar,
    ) -> tuple[Signal | None, dict[str, Any]]:
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
        net_rr = (
            net_reward / net_risk
            if geometry_ok and net_risk > 0.0 and net_reward > 0.0
            else None
        )
        diagnostic: dict[str, Any] = {
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
            "stop_model": "OBSERVED_UNACCEPTED_SWEEP_EXTREME",
            "target_model": "SOURCE_AUCTION_EQUILIBRIUM",
        }
        rejection: str | None = None
        if not geometry_ok:
            rejection = "UNACCEPTED_SWEEP_HAS_INVALID_TARGET_GEOMETRY"
        elif net_risk <= 0.0 or net_reward <= 0.0:
            rejection = "UNACCEPTED_SWEEP_HAS_NONPOSITIVE_REWARD_AFTER_COST"
        elif net_rr is None or net_rr < self.config.minimum_net_reward_to_risk:
            rejection = "UNACCEPTED_SWEEP_NET_REWARD_TO_RISK_BELOW_GATE"
        if rejection is not None:
            diagnostic["rejection_reason"] = rejection
            return None, diagnostic

        reason = "UNACCEPTED_LIQUIDITY_SWEEP_PASSIVELY_ABSORBED_TO_EQUILIBRIUM"
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
            details={**diagnostic, "entry_order_type": "MARKET"},
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
