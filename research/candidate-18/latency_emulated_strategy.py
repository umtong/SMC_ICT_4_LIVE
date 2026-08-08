"""Candidate 18 latency-safe execution overlay.

The first development run proved the causal router but exposed a concrete
execution defect: five native STOP_LIMIT parents reached the venue one bar
after submission with their triggers already crossed. Nautilus rejected the
whole three-order bracket, producing 15 rejection callbacks.

This overlay keeps the same directional re-arm trigger and worst-fill cap, but
uses NautilusTrader's built-in local order emulation. The conditional parent is
held by the OrderEmulator and is released as a capped LIMIT only when its
trigger is observed. The same bracket remains responsible for protective and
target orders; no custom matching, portfolio, or account engine is introduced.
"""
from __future__ import annotations

import math

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.enums import TriggerType

from execution_preserving_strategy import Candidate18Config
from execution_preserving_strategy import Candidate18Strategy as _Candidate18Strategy
from logic import choose_liquidity_target
from logic import floor_quantity
from logic import planned_loss_per_unit
from strategy_base import PendingSetup


class Candidate18Strategy(_Candidate18Strategy):
    """Execute Candidate 18 brackets through Nautilus local emulation."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        self.diagnostics["candidate18_emulated_stop_limit_entries"] = 0

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        """Submit a locally emulated, price-capped STOP_LIMIT bracket."""
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
            self._expire_pending(row, "INVALID_EMULATED_STOP_LIMIT_GEOMETRY")
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
            self._expire_pending(row, "INVALID_LONG_EMULATED_STOP_LIMIT_BRACKET")
            return False
        if side < 0 and not (target < entry_limit <= entry_trigger < stop):
            self._expire_pending(row, "INVALID_SHORT_EMULATED_STOP_LIMIT_BRACKET")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.GTC,
            emulation_trigger=TriggerType.DEFAULT,
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
        self.diagnostics["candidate18_emulated_stop_limit_entries"] = int(
            self.diagnostics["candidate18_emulated_stop_limit_entries"],
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
            "DIRECTIONAL_REARM_WITH_LOCALLY_EMULATED_PRICE_CAP",
            entry_trigger,
            {
                **setup.details,
                "branch": setup.branch,
                "side": side,
                "signal_close": signal_close,
                "entry_trigger": entry_trigger,
                "entry_limit_worst_fill": entry_limit,
                "entry_emulation_trigger": "DEFAULT_BID_ASK",
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
