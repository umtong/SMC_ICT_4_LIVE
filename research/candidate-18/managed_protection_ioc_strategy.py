"""Candidate 18 v4: price-capped IOC with fill-by-fill managed protection.

Candidate 18 v1/v3 proved that the frozen NautilusTrader 1.230.0 OTO path can
cancel protective children when an IOC parent partially fills and its remainder
cancels. This strategy removes the causal dependency between entry and exits.
The entry is one standalone price-capped IOC LIMIT. Every actual entry fill
creates an independent reduce-only STOP_MARKET and LIMIT target for exactly the
filled quantity. Parent cancellation therefore cannot cancel protection.

If one protective order fills, all stale sibling protectives are canceled and
the remaining tracked position is re-protected on the resulting position event.
NautilusTrader still owns matching, fills, orders, positions, margin and NAV.
"""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.enums import OrderSide, TimeInForce

from execution_preserving_strategy import Candidate18Config
from execution_preserving_strategy import Candidate18Strategy as _Candidate18Strategy
from logic import choose_liquidity_target, floor_quantity, planned_loss_per_unit
from strategy_base import PendingSetup


def _number(value: Any) -> float:
    method = getattr(value, "as_double", None)
    if callable(method):
        return float(method())
    return float(str(value).strip().split()[0].replace(",", ""))


class Candidate18Strategy(_Candidate18Strategy):
    """Protect each real IOC fill independently from the entry lifecycle."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        self._managed_entry_id: str | None = None
        self._managed_side = 0
        self._managed_stop = float("nan")
        self._managed_target = float("nan")
        self._managed_open_qty = 0.0
        self._pending_protection_qty = 0.0
        self._protective_ids: set[str] = set()
        self._tranche_counter = 0
        self.diagnostics.update(
            {
                "candidate18_v4_entry_fill_events": 0,
                "candidate18_v4_entry_fill_qty": 0.0,
                "candidate18_v4_protection_batches": 0,
                "candidate18_v4_stop_qty_submitted": 0.0,
                "candidate18_v4_target_qty_submitted": 0.0,
                "candidate18_v4_exit_fill_events": 0,
                "candidate18_v4_protective_rejections": 0,
                "candidate18_v4_crossed_stop_fail_closes": 0,
                "candidate18_v4_max_pending_unprotected_qty": 0.0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._managed_entry_id = None
        self._managed_side = 0
        self._managed_stop = float("nan")
        self._managed_target = float("nan")
        self._managed_open_qty = 0.0
        self._pending_protection_qty = 0.0
        self._protective_ids.clear()
        self._tranche_counter = 0

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
            self._expire_pending(row, "INVALID_MANAGED_IOC_GEOMETRY")
            return False
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
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
            self._expire_pending(row, "INVALID_LONG_MANAGED_IOC")
            return False
        if side < 0 and not (target < entry_limit < signal_close < stop):
            self._expire_pending(row, "INVALID_SHORT_MANAGED_IOC")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        entry_order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            price=self.instrument.make_price(entry_limit),
            time_in_force=TimeInForce.IOC,
            post_only=False,
            reduce_only=False,
            tags=["ENTRY", "CANDIDATE18_MANAGED_IOC_ENTRY"],
        )
        self._managed_entry_id = str(entry_order.client_order_id)
        self._managed_side = side
        self._managed_stop = stop
        self._managed_target = target
        self.submit_order(entry_order)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = setup.branch
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] = int(self.diagnostics["entry_submissions"]) + 1
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
            "STANDALONE_PRICE_CAPPED_IOC_WITH_FILL_MANAGED_PROTECTION",
            entry_limit,
            {
                **setup.details,
                "candidate18_version": "v4-managed-protection",
                "branch": setup.branch,
                "side": side,
                "signal_close": signal_close,
                "entry_limit_worst_fill": entry_limit,
                "entry_time_in_force": "IOC",
                "entry_client_order_id": self._managed_entry_id,
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

    def _stop_crossed(self, fill_price: float) -> bool:
        return (
            (self._managed_side > 0 and fill_price <= self._managed_stop)
            or (self._managed_side < 0 and fill_price >= self._managed_stop)
        )

    def _fail_close(self, event: Any, reason: str) -> None:
        self.protective_fail_close_pending = True
        self.entry_pending = False
        self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id)
            self.exit_order_submitted = True
            self.exit_request_index = self.bar_index
        if self.current_scenario_id is not None:
            ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
            self._transition(
                self.current_scenario_id,
                "MANAGED_PROTECTION_FAIL_CLOSE",
                ts,
                ts,
                "PROTECTIVE_EXIT_PENDING",
                reason,
                float(self.bars[-1]["close"]),
                {"event": str(event)},
            )

    def _submit_pending_protection(self, event: Any | None = None) -> None:
        quantity_value = self._pending_protection_qty
        if quantity_value <= 1e-12:
            return
        if self._managed_side == 0:
            self._fail_close(event, "MISSING_MANAGED_ENTRY_SIDE")
            return
        fill_price = _number(getattr(event, "last_px", self.bars[-1]["close"])) if event is not None else float(self.bars[-1]["close"])
        if self._stop_crossed(fill_price):
            self.diagnostics["candidate18_v4_crossed_stop_fail_closes"] = int(
                self.diagnostics["candidate18_v4_crossed_stop_fail_closes"],
            ) + 1
            self._fail_close(event, "ACTUAL_FILL_ALREADY_CROSSED_MANAGED_STOP")
            return

        self._tranche_counter += 1
        exit_side = OrderSide.SELL if self._managed_side > 0 else OrderSide.BUY
        tags = [
            f"CANDIDATE18_MANAGED_TRANCHE_{self._tranche_counter}",
            str(self.current_scenario_id or "UNKNOWN"),
        ]
        stop_order = self.order_factory.stop_market(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=self.instrument.make_qty(quantity_value),
            trigger_price=self.instrument.make_price(self._managed_stop),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            tags=["CANDIDATE18_MANAGED_STOP", *tags],
        )
        target_order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=self.instrument.make_qty(quantity_value),
            price=self.instrument.make_price(self._managed_target),
            time_in_force=TimeInForce.GTC,
            post_only=False,
            reduce_only=True,
            tags=["CANDIDATE18_MANAGED_TARGET", *tags],
        )
        self._protective_ids.update(
            {str(stop_order.client_order_id), str(target_order.client_order_id)},
        )
        self.submit_order(stop_order)
        self.submit_order(target_order)
        self.diagnostics["candidate18_v4_protection_batches"] = int(
            self.diagnostics["candidate18_v4_protection_batches"],
        ) + 1
        self.diagnostics["candidate18_v4_stop_qty_submitted"] = float(
            self.diagnostics["candidate18_v4_stop_qty_submitted"],
        ) + quantity_value
        self.diagnostics["candidate18_v4_target_qty_submitted"] = float(
            self.diagnostics["candidate18_v4_target_qty_submitted"],
        ) + quantity_value
        self._pending_protection_qty = 0.0

    def on_order_filled(self, event: Any) -> None:
        client_order_id = str(getattr(event, "client_order_id", ""))
        fill_qty = _number(getattr(event, "last_qty", 0.0))
        if client_order_id == self._managed_entry_id:
            self._managed_open_qty += fill_qty
            self._pending_protection_qty += fill_qty
            self.entry_pending = False
            self.diagnostics["candidate18_v4_entry_fill_events"] = int(
                self.diagnostics["candidate18_v4_entry_fill_events"],
            ) + 1
            self.diagnostics["candidate18_v4_entry_fill_qty"] = float(
                self.diagnostics["candidate18_v4_entry_fill_qty"],
            ) + fill_qty
            self.diagnostics["candidate18_v4_max_pending_unprotected_qty"] = max(
                float(self.diagnostics["candidate18_v4_max_pending_unprotected_qty"]),
                self._pending_protection_qty,
            )
            self._submit_pending_protection(event)
            return

        if client_order_id in self._protective_ids:
            self._managed_open_qty = max(0.0, self._managed_open_qty - fill_qty)
            self.diagnostics["candidate18_v4_exit_fill_events"] = int(
                self.diagnostics["candidate18_v4_exit_fill_events"],
            ) + 1
            self.cancel_all_orders(self.config.instrument_id)
            self._protective_ids.clear()
            if self._managed_open_qty > 1e-12:
                self._pending_protection_qty = self._managed_open_qty
            return

    def on_position_opened(self, event: Any) -> None:
        super().on_position_opened(event)
        if self.protective_fail_close_pending:
            self._fail_close(event, "POSITION_OPENED_AFTER_PROTECTION_FAILURE")
            return
        self._submit_pending_protection(event)

    def on_position_changed(self, event: Any) -> None:
        if self._pending_protection_qty > 1e-12:
            self._submit_pending_protection(event)

    def on_position_closed(self, event: Any) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        super().on_position_closed(event)

    def on_order_rejected(self, event: Any) -> None:
        client_order_id = str(getattr(event, "client_order_id", ""))
        if client_order_id not in self._protective_ids:
            super().on_order_rejected(event)
            return
        self.diagnostics["order_rejections"] = int(self.diagnostics["order_rejections"]) + 1
        self.diagnostics["candidate18_v4_protective_rejections"] = int(
            self.diagnostics["candidate18_v4_protective_rejections"],
        ) + 1
        self._fail_close(event, "MANAGED_PROTECTIVE_ORDER_REJECTED")


__all__ = ["Candidate18Config", "Candidate18Strategy"]
