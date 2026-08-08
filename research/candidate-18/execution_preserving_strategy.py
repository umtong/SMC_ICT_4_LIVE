"""Candidate 18: execution-preserving failed-auction/acceptance router.

Candidate 17 showed that its remembered-defense continuation branch never
confirmed on the untouched week, while early reversal initiatives and market
entry latency produced most losses. Candidate 18 therefore changes two
specific economic decisions and leaves NautilusTrader accounting/execution
ownership intact:

* remembered defense without proven depletion closes unresolved instead of
  carrying an inactive branch;
* a later reversal initiative must be persistent or an immediate notional
  shock, then re-arm through a price-capped STOP_LIMIT bracket rather than a
  next-bar MARKET entry.

True-acceptance routing, causal feature contracts, natural-liquidity targets,
risk budgeting, fees, funding, portfolio accounting and fail-close protection
remain inherited from Candidates 17/16/05.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from displayed_liquidity_router import InitiativeDecision
from displayed_liquidity_router import InitiativeObservation
from displayed_liquidity_router import advance_failure_leg
from initiative_quality_router import InitiativeRoute
from initiative_quality_router import classify_initiative_quality
from logic import choose_liquidity_target
from logic import floor_quantity
from logic import planned_loss_per_unit
from remembered_defense_strategy import Candidate17Config
from remembered_defense_strategy import Candidate17Strategy
from strategy_base import PendingSetup


class Candidate18Config(Candidate17Config, frozen=True):
    initiative_shock_burst_min: float = 1.0
    entry_rearm_atr: float = 0.01
    entry_limit_risk_expansion: float = 0.50


class Candidate18Strategy(Candidate17Strategy):
    """Route only persistent/shock initiative and preserve entry geometry."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        if config.initiative_shock_burst_min < 1.0:
            raise ValueError("initiative_shock_burst_min must be >= 1")
        if config.entry_rearm_atr <= 0.0:
            raise ValueError("entry_rearm_atr must be positive")
        if not 0.0 < config.entry_limit_risk_expansion <= 1.0:
            raise ValueError("entry_limit_risk_expansion must be in (0, 1]")
        self.diagnostics.update(
            {
                "candidate18_remembered_defense_no_trade": 0,
                "candidate18_initiative_sustained": 0,
                "candidate18_initiative_shock": 0,
                "candidate18_initiative_quality_rejected": 0,
                "candidate18_stop_limit_entries": 0,
            },
        )

    def _arm_defense_memory(
        self,
        *,
        setup: PendingSetup,
        row: dict[str, float | int],
        details: dict[str, Any],
        cause: str,
        last_index: int,
    ) -> None:
        """Close repeated defense unresolved; Candidate 17 proved no active leg."""
        del last_index
        self.diagnostics["candidate16_unresolved"] = int(
            self.diagnostics["candidate16_unresolved"],
        ) + 1
        self.diagnostics["candidate18_remembered_defense_no_trade"] = int(
            self.diagnostics["candidate18_remembered_defense_no_trade"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "REMEMBERED_DEFENSE_CLOSED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            "REPEATED_DEFENSE_HAS_NO_CAUSAL_DEPLETION_PROOF",
            float(row["close"]),
            {**details, "candidate18_closed_cause": cause},
        )
        self.pending = None
        self.parent_auction = None
        self.failure_leg = None
        self.defense_memory = None

    def _process_failure_initiative(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        state = self.failure_leg
        if setup is None or state is None:
            self._expire_pending(row, "MISSING_FROZEN_FAILURE_STATE")
            return True
        if self.bar_index <= setup.created_index:
            return True

        state = advance_failure_leg(
            state,
            InitiativeObservation(
                bar_index=self.bar_index,
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                flow_60s=self._feature("flow_60s"),
                ret_60s_bps=self._feature("ret_60s_bps"),
                depth_imbalance_1=self._feature("depth_imbalance_1"),
                liquidity_ahead_change_1m=self._feature(
                    self._ahead_depth_field(state.side),
                ),
            ),
        )
        self.failure_leg = state
        setup.details["latest_failure_leg"] = {
            **asdict(state),
            "decision": state.decision.value,
        }
        self._transition(
            setup.scenario_id,
            "FAILURE_INITIATIVE_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            "FAILURE_FROZEN"
            if state.decision is InitiativeDecision.WAITING
            else state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )
        if state.decision is InitiativeDecision.WAITING:
            return True
        if state.decision is InitiativeDecision.INVALIDATED:
            self.diagnostics["candidate16_v2_failure_initiative_invalidated"] = int(
                self.diagnostics["candidate16_v2_failure_initiative_invalidated"],
            ) + 1
            self.pending = None
            self.failure_leg = None
            return True
        if state.decision is InitiativeDecision.EXPIRED:
            self.diagnostics["candidate16_v2_failure_initiative_expired"] = int(
                self.diagnostics["candidate16_v2_failure_initiative_expired"],
            ) + 1
            self.pending = None
            self.failure_leg = None
            return True

        quality = classify_initiative_quality(
            observations=int(state.observations),
            max_wait_bars=int(state.max_wait_bars),
            notional_burst=self._feature("notional_burst"),
            shock_burst_min=self.config.initiative_shock_burst_min,
        )
        setup.details["candidate18_initiative_quality"] = {
            "route": quality.route.value,
            "reason": quality.reason,
            "notional_burst": self._feature("notional_burst"),
            "observations": int(state.observations),
            "max_wait_bars": int(state.max_wait_bars),
            "oi_change_5m": self._feature("oi_change_5m"),
            "metrics_age_seconds": self._feature("metrics_age_seconds"),
        }
        if quality.route is InitiativeRoute.UNRESOLVED:
            self.diagnostics["candidate18_initiative_quality_rejected"] = int(
                self.diagnostics["candidate18_initiative_quality_rejected"],
            ) + 1
            self._transition(
                setup.scenario_id,
                "INITIATIVE_QUALITY_REJECTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                quality.reason,
                float(row["close"]),
                setup.details,
            )
            self.pending = None
            self.failure_leg = None
            return True

        key = (
            "candidate18_initiative_sustained"
            if quality.route is InitiativeRoute.SUSTAINED
            else "candidate18_initiative_shock"
        )
        self.diagnostics[key] = int(self.diagnostics[key]) + 1
        completed = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="REJECTION",
            side=setup.side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.structure,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=False,
            details={
                **setup.details,
                "candidate18_branch": (
                    "FAILED_AUCTION_SUSTAINED_INITIATIVE"
                    if quality.route is InitiativeRoute.SUSTAINED
                    else "FAILED_AUCTION_NOTIONAL_SHOCK"
                ),
                "confirmed_failure_leg": {
                    **asdict(state),
                    "decision": state.decision.value,
                },
            },
        )
        self.pending = completed
        self.failure_leg = None
        self.diagnostics["candidate16_v2_failure_initiatives"] = int(
            self.diagnostics["candidate16_v2_failure_initiatives"],
        ) + 1
        self._transition(
            completed.scenario_id,
            "FAILURE_INITIATIVE_QUALITY_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_REARM_EVALUATION",
            quality.reason,
            float(row["close"]),
            completed.details,
        )
        self._submit_entry(completed, row)
        return True

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        """Submit a capped STOP_LIMIT parent so stale next-bar market fills vanish."""
        atr = self._atr()
        side = setup.side
        signal_close = float(row["close"])
        if setup.branch == "REJECTION":
            stop = setup.sweep_extreme - side * self.config.stop_buffer_atr * atr
            fallback_r = self.config.rejection_target_net_r
        else:
            if side > 0:
                stop = min(
                    setup.pool_level - self.config.stop_buffer_atr * atr,
                    float(row["low"]) - 0.25 * self.config.stop_buffer_atr * atr,
                )
            else:
                stop = max(
                    setup.pool_level + self.config.stop_buffer_atr * atr,
                    float(row["high"]) + 0.25 * self.config.stop_buffer_atr * atr,
                )
            fallback_r = self.config.acceptance_target_net_r

        structural_risk = abs(signal_close - stop)
        if not math.isfinite(structural_risk) or structural_risk <= 0.0:
            self._expire_pending(row, "INVALID_STOP_GEOMETRY")
            return False
        entry_trigger = signal_close + side * self.config.entry_rearm_atr * atr
        entry_limit = entry_trigger + (
            side * self.config.entry_limit_risk_expansion * structural_risk
        )

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse_slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry_limit,
            stop,
            side,
            cost_rate,
            adverse_slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._expire_pending(row, "INVALID_STOP_LIMIT_GEOMETRY")
            return False
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / planned_loss
        quantity_value = floor_quantity(raw_quantity, int(self.instrument.size_precision))
        if quantity_value <= 0.0 or quantity_value * entry_limit < 10.0:
            self._expire_pending(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        target, target_source, target_r = choose_liquidity_target(
            entry=entry_limit,
            side=side,
            pools=list(self.active_pools.values()),
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            min_net_r=self.config.min_target_net_r,
            max_net_r=self.config.max_target_net_r,
            fallback_net_r=fallback_r,
        )
        if side > 0 and not (stop < entry_trigger <= entry_limit < target):
            self._expire_pending(row, "INVALID_LONG_STOP_LIMIT_BRACKET")
            return False
        if side < 0 and not (target < entry_limit <= entry_trigger < stop):
            self._expire_pending(row, "INVALID_SHORT_STOP_LIMIT_BRACKET")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.GTC,
            entry_order_type=OrderType.STOP_LIMIT,
            entry_trigger_price=self.instrument.make_price(entry_trigger),
            entry_price=self.instrument.make_price(entry_limit),
            entry_post_only=False,
            tp_price=self.instrument.make_price(target),
            sl_trigger_price=self.instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = setup.branch
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["candidate18_stop_limit_entries"] = int(
            self.diagnostics["candidate18_stop_limit_entries"],
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "DIRECTIONAL_REARM_WITH_PRICE_CAPPED_STOP_LIMIT_BRACKET",
            entry_trigger,
            {
                **setup.details,
                "branch": setup.branch,
                "side": side,
                "signal_close": signal_close,
                "entry_trigger": entry_trigger,
                "entry_limit_worst_fill": entry_limit,
                "stop": stop,
                "target": target,
                "target_source": target_source,
                "target_net_r": target_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_at_worst_fill": planned_loss,
                "planned_account_loss_at_worst_fill": quantity_value * planned_loss,
            },
        )
        return True


__all__ = ["Candidate18Config", "Candidate18Strategy"]
