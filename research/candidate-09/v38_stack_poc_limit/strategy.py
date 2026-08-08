"""Candidate 09 v38: V35 signal with native GTD limit at footprint POC.

V35's completed-auction context and footprint acceptance remain frozen. The only
changed variable is execution: after the same defended retest, submit a passive
GTD limit at the already-observed footprint POC/stack zone instead of a market
bracket. Stop, natural objective, costs, 3% current-NAV risk and max two-bar
entry lifetime remain unchanged. The exact control calls V35's market entry.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from logic import choose_liquidity_target
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import PendingSetup
from strategy_v35 import Candidate16Config as _Candidate35Config
from strategy_v35 import Candidate16Strategy as _Candidate35Strategy


class Candidate16Config(_Candidate35Config, frozen=True):
    candidate38_use_stack_limit: bool = True
    candidate38_limit_expiry_bars: int = 2


class Candidate16Strategy(_Candidate35Strategy):
    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "candidate38_stack_limit_submissions": 0,
                "candidate38_stack_limit_invalid": 0,
                "candidate38_market_control_submissions": 0,
            }
        )

    def _tick_size(self) -> float:
        value = getattr(self.instrument, "price_increment", None)
        method = getattr(value, "as_double", None)
        if callable(method):
            return float(method())
        return float(str(value).split()[0])

    def _stack_limit_price(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> float | None:
        side = setup.side
        close = float(row["close"])
        tick = self._tick_size()
        poc = float(setup.details.get("candidate33_stack_poc", math.nan))
        low = float(setup.details.get("candidate33_stack_low", math.nan))
        high = float(setup.details.get("candidate33_stack_high", math.nan))
        if not (math.isfinite(poc) and math.isfinite(low) and math.isfinite(high)):
            return None
        if side > 0:
            candidate = min(poc, high, close - tick)
            candidate = max(candidate, setup.pool_level + tick)
            if not (setup.pool_level < candidate < close):
                return None
        else:
            candidate = max(poc, low, close + tick)
            candidate = min(candidate, setup.pool_level - tick)
            if not (close < candidate < setup.pool_level):
                return None
        return round(candidate / tick) * tick

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        if not self.config.candidate38_use_stack_limit:
            self.diagnostics["candidate38_market_control_submissions"] = int(
                self.diagnostics["candidate38_market_control_submissions"]
            ) + 1
            return super()._submit_entry(setup, row)
        if setup.branch != "ACCEPTANCE":
            return super()._submit_entry(setup, row)

        entry = self._stack_limit_price(setup, row)
        if entry is None:
            self.diagnostics["candidate38_stack_limit_invalid"] = int(
                self.diagnostics["candidate38_stack_limit_invalid"]
            ) + 1
            self._expire_pending(row, "STACK_POC_NOT_A_PASSIVE_VALID_ENTRY")
            return False

        atr = self._atr()
        side = setup.side
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

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._expire_pending(row, "INVALID_STACK_LIMIT_STOP_GEOMETRY")
            return False

        objective_kind = "HIGH" if side > 0 else "LOW"
        objectives = [
            pool
            for pool in self.active_pools.values()
            if pool.kind == objective_kind
            and side * (pool.level - entry) > 0.0
            and net_r_at_price(entry, pool.level, side, planned_loss, cost_rate)
            >= self.config.min_target_net_r
        ]
        if not objectives:
            self.diagnostics["candidate16_natural_objective_rejections"] = int(
                self.diagnostics["candidate16_natural_objective_rejections"]
            ) + 1
            self._expire_pending(
                row,
                "NO_UNCONSUMED_LIQUIDITY_OBJECTIVE_AFTER_STACK_LIMIT_COSTS",
            )
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / planned_loss
        quantity_value = floor_quantity(
            raw_quantity,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self._expire_pending(row, "STACK_LIMIT_QUANTITY_BELOW_MINIMUM")
            return False

        target, target_source, target_r = choose_liquidity_target(
            entry=entry,
            side=side,
            pools=list(self.active_pools.values()),
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            min_net_r=self.config.min_target_net_r,
            max_net_r=self.config.max_target_net_r,
            fallback_net_r=self.config.acceptance_target_net_r,
        )
        if side > 0 and not (stop < entry < target):
            self._expire_pending(row, "INVALID_LONG_STACK_LIMIT_BRACKET")
            return False
        if side < 0 and not (target < entry < stop):
            self._expire_pending(row, "INVALID_SHORT_STACK_LIMIT_BRACKET")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        expire = (
            datetime.fromtimestamp(int(row["ts"]) / 1_000_000_000, tz=timezone.utc)
            + timedelta(minutes=self.config.candidate38_limit_expiry_bars)
            + timedelta(microseconds=1)
        )
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.LIMIT,
            entry_price=self.instrument.make_price(entry),
            time_in_force=TimeInForce.GTD,
            expire_time=expire,
            entry_post_only=True,
            tp_order_type=OrderType.LIMIT,
            tp_price=self.instrument.make_price(target),
            tp_time_in_force=TimeInForce.GTC,
            tp_post_only=True,
            sl_order_type=OrderType.STOP_MARKET,
            sl_trigger_price=self.instrument.make_price(stop),
            sl_time_in_force=TimeInForce.GTC,
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
            self.diagnostics["entry_submissions"]
        ) + 1
        self.diagnostics["candidate38_stack_limit_submissions"] = int(
            self.diagnostics["candidate38_stack_limit_submissions"]
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "STACK_POC_LIMIT_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "PASSIVE_GTD_ENTRY_AT_OBSERVED_FOOTPRINT_POC",
            entry,
            {
                **setup.details,
                "branch": setup.branch,
                "side": side,
                "entry_estimate": entry,
                "entry_order_type": "LIMIT_GTD_POST_ONLY",
                "expire_time": expire.isoformat(),
                "stop": stop,
                "target": target,
                "target_source": target_source,
                "target_net_r": target_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
                "planned_account_loss": quantity_value * planned_loss,
            },
        )
        return True


__all__ = ["Candidate16Config", "Candidate16Strategy"]
