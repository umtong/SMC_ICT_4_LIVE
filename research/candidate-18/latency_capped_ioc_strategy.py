"""Candidate 18 final execution overlay: price-capped IOC parent.

The causal signal is complete at the finished one-minute bar. Candidate 17's
next-bar market parent could fill beyond the planned stop. Native STOP_LIMIT
orders then proved incompatible with the bar replay latency because their
triggers could be crossed before venue insertion; BID/ASK emulation could not
trigger because the shared runner intentionally contains bars rather than
quotes.

This overlay therefore uses NautilusTrader's native IOC LIMIT parent as a
marketable limit with an explicit worst price. The parent fills immediately at
or better than the cap or cancels. Protective and target children remain the
same bracket, and risk is sized from the cap including all configured costs.
"""
from __future__ import annotations

import math

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TimeInForce

from execution_preserving_strategy import Candidate18Config
from execution_preserving_strategy import Candidate18Strategy as _Candidate18Strategy
from logic import choose_liquidity_target
from logic import floor_quantity
from logic import planned_loss_per_unit
from strategy_base import PendingSetup


class Candidate18Strategy(_Candidate18Strategy):
    """Execute completed causal signals with a bounded IOC LIMIT bracket."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        self.diagnostics["candidate18_ioc_limit_entries"] = 0

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
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

        # A completed initiative/retest already owns directional confirmation.
        # The limit is therefore an execution cap, not a new state signal.
        cap_distance = max(
            self.config.entry_rearm_atr * atr,
            self.config.entry_limit_risk_expansion * structural_risk,
        )
        entry_limit = signal_close + side * cap_distance

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
            self._expire_pending(row, "INVALID_IOC_LIMIT_GEOMETRY")
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
        if side > 0 and not (stop < signal_close < entry_limit < target):
            self._expire_pending(row, "INVALID_LONG_IOC_LIMIT_BRACKET")
            return False
        if side < 0 and not (target < entry_limit < signal_close < stop):
            self._expire_pending(row, "INVALID_SHORT_IOC_LIMIT_BRACKET")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.LIMIT,
            entry_price=self.instrument.make_price(entry_limit),
            time_in_force=TimeInForce.IOC,
            entry_post_only=False,
            entry_tags=["ENTRY", "CANDIDATE18_IOC_PRICE_CAP"],
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
        self.diagnostics["candidate18_ioc_limit_entries"] = int(
            self.diagnostics["candidate18_ioc_limit_entries"],
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
            "COMPLETED_SIGNAL_WITH_PRICE_CAPPED_IOC_LIMIT",
            entry_limit,
            {
                **setup.details,
                "branch": setup.branch,
                "side": side,
                "signal_close": signal_close,
                "entry_limit_worst_fill": entry_limit,
                "entry_time_in_force": "IOC",
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
