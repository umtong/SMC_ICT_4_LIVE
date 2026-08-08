"""Market-entry execution adapter for the external-liquidity exhaustion alpha.

The inherited sparse aggTrade stream exists to advance NautilusTrader's clock
through the configured latency.  It intentionally retains only one actual
aggregate trade per minute and therefore is not a complete liquidity book.
Using the retained tick quantity as an all-or-none FOK capacity test makes a
large but liquid BTC order structurally unfillable.  This adapter keeps the
same alpha, state transition, stop, target, fees, latency, and 3% NAV risk, but
uses NautilusTrader's native MARKET bracket for execution.  Quantity and
minimum-net-R are calculated at a volatility-aware adverse fill budget.  Any
actual fill worse than that budget is counted as an integrity breach.
"""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from candidate21_strategy import Candidate21Config
from candidate21_strategy import Candidate21Strategy
from logic import floor_quantity, net_r_at_price, planned_loss_per_unit
from strategy_base import PendingSetup, _as_float


MarketEntryConfig = Candidate21Config


class MarketEntryStrategy(Candidate21Strategy):
    """Execute validated exhaustion reversals without sparse-tick FOK bias."""

    def __init__(self, config: Candidate21Config) -> None:
        super().__init__(config=config)
        self.current_entry_worst_fill: float | None = None
        self.current_entry_side = 0
        self.diagnostics.update(
            {
                "exhaustion_market_entries": 0,
                "entry_worst_fill_breaches": 0,
                "max_entry_worst_fill_slippage_bps": 0.0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.current_entry_worst_fill = None
        self.current_entry_side = 0

    def on_position_opened(self, event: Any) -> None:
        fill = float(getattr(event, "avg_px_open", float("nan")))
        worst = self.current_entry_worst_fill
        side = self.current_entry_side
        if worst is not None and side in (-1, 1) and math.isfinite(fill) and fill > 0.0:
            signed_slippage_bps = side * (fill / worst - 1.0) * 10_000.0
            self.diagnostics["max_entry_worst_fill_slippage_bps"] = max(
                float(self.diagnostics["max_entry_worst_fill_slippage_bps"]),
                signed_slippage_bps,
            )
            self.diagnostics["last_entry_actual_fill"] = fill
            self.diagnostics["last_entry_worst_fill_budget"] = worst
            self.diagnostics["last_entry_fill_vs_budget_bps"] = signed_slippage_bps
            if signed_slippage_bps > 1e-9:
                self.diagnostics["entry_worst_fill_breaches"] = int(
                    self.diagnostics["entry_worst_fill_breaches"],
                ) + 1
        super().on_position_opened(event)

    def _submit_exhaustion_entry(
        self,
        *,
        setup: PendingSetup,
        row: dict[str, float | int],
        side: int,
        stop_raw: float,
        target_raw: float,
    ) -> bool:
        signal_close = float(row["close"])
        stop_price = self.instrument.make_price(stop_raw)
        stop = _as_float(stop_price)
        atr = self._atr()
        transition_range = max(0.0, float(row["high"]) - float(row["low"]))
        adverse_fill_distance = max(
            signal_close
            * max(
                self.config.exhaustion_entry_cap_bps,
                self.config.adverse_slippage_bps_each_side,
            )
            / 10_000.0,
            self.config.exhaustion_entry_cap_atr * atr,
            0.5 * transition_range,
        )
        worst_entry_price = self.instrument.make_price(
            signal_close + side * adverse_fill_distance,
        )
        worst_entry = _as_float(worst_entry_price)
        increment = _as_float(self.instrument.price_increment)
        if side > 0 and worst_entry <= signal_close:
            worst_entry_price = self.instrument.make_price(signal_close + increment)
            worst_entry = _as_float(worst_entry_price)
        elif side < 0 and worst_entry >= signal_close:
            worst_entry_price = self.instrument.make_price(signal_close - increment)
            worst_entry = _as_float(worst_entry_price)
        target_price = self.instrument.make_price(target_raw)
        target = _as_float(target_price)

        if (side > 0 and not stop < signal_close < worst_entry < target) or (
            side < 0 and not target < worst_entry < signal_close < stop
        ):
            self.diagnostics["exhaustion_target_consumed"] = int(
                self.diagnostics["exhaustion_target_consumed"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_TARGET_CONSUMED_BEFORE_ENTRY",
                "WORST_FILL_STOP_TARGET_DO_NOT_BELONG_TO_ONE_REMAINING_REVERSAL_LEG",
            )
            return True

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            worst_entry,
            stop,
            side,
            cost_rate,
            adverse,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["exhaustion_geometry_rejected"] = int(
                self.diagnostics["exhaustion_geometry_rejected"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_ENTRY_GEOMETRY_REJECTED",
                "INVALID_ADVERSE_FILL_PLANNED_LOSS",
            )
            return True
        target_r = net_r_at_price(
            worst_entry,
            target,
            side,
            planned_loss,
            cost_rate,
        )
        if target_r + 1e-9 < self.config.min_target_net_r:
            self.diagnostics["exhaustion_geometry_rejected"] = int(
                self.diagnostics["exhaustion_geometry_rejected"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_ENTRY_GEOMETRY_REJECTED",
                "NATURAL_EXTERNAL_LIQUIDITY_OBJECTIVE_BELOW_MINIMUM_NET_R",
            )
            return True

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * worst_entry < 10.0:
            self.diagnostics["exhaustion_geometry_rejected"] = int(
                self.diagnostics["exhaustion_geometry_rejected"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_ENTRY_GEOMETRY_REJECTED",
                "QUANTITY_BELOW_INSTRUMENT_MINIMUM",
            )
            return True

        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.MARKET,
            time_in_force=TimeInForce.IOC,
            entry_post_only=False,
            entry_tags=["ENTRY", "EXTERNAL_EXHAUSTION_MARKET_IOC"],
            tp_price=target_price,
            sl_trigger_price=stop_price,
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = "EXTERNAL_LIQUIDITY_EXHAUSTION_REVERSAL"
        self.current_pool_level = setup.pool_level
        self.current_entry_worst_fill = worst_entry
        self.current_entry_side = side
        self.pending = None
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["exhaustion_market_entries"] = int(
            self.diagnostics["exhaustion_market_entries"],
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "EXHAUSTION_MARKET_ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "EXTERNAL_LIQUIDITY_EXHAUSTION_REVERSAL_MARKET_IOC",
            worst_entry,
            {
                **setup.details,
                "side": side,
                "signal_close": signal_close,
                "entry_order_type": "MARKET",
                "entry_time_in_force": "IOC",
                "entry_worst_fill_budget": worst_entry,
                "entry_adverse_fill_distance": adverse_fill_distance,
                "entry_cap_atr_multiple": self.config.exhaustion_entry_cap_atr,
                "entry_transition_range": transition_range,
                "stop": stop,
                "target": target,
                "target_net_r_at_worst_fill": target_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_at_worst_fill": planned_loss,
                "planned_account_loss_at_worst_fill": quantity_value * planned_loss,
                "strictly_later_entry_evidence": True,
                "sparse_ticks_used_as_clock_not_liquidity_capacity": True,
            },
        )
        return True


__all__ = ["MarketEntryConfig", "MarketEntryStrategy"]
