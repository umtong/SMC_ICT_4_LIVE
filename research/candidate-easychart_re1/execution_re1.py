"""Venue-compatible entry and protection lifecycle for EasyChart RE1.

NautilusTrader bracket objects are useful in simulation, but Binance USD-M does
not provide the same native parent/OCO bracket contract.  RE1 therefore uses the
same explicit order lifecycle in backtest and live/demo:

1. submit one market entry;
2. as soon as the first fill opens a position, submit one reduce-only stop-market
   and one reduce-only take-profit limit for the actual open quantity;
3. keep both protective leaves quantities synchronized with later partial entry
   or partial exit fills;
4. when the position closes, cancel the surviving sibling before releasing the
   global account slot;
5. any missing/rejected protective while exposure remains causes an immediate
   cancel-all plus market flatten request.

Entry, stop and target prices remain those fixed in the pre-entry trade plan.
This module changes order transport only; it does not add trade management.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from nautilus_trader.model.enums import OrderSide, TimeInForce, TriggerType
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCancelRejected,
    OrderCanceled,
    OrderDenied,
    OrderExpired,
    OrderFilled,
    OrderModifyRejected,
    OrderRejected,
    OrderUpdated,
    PositionChanged,
    PositionClosed,
    PositionOpened,
)
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId

from domain import Side
from mtf_strategy_v5 import EasyChartMTFConfig, EasyChartMTFStrategy


LIVE_PROTECTION_POLICY = (
    "EXTERNAL_METHOD:MARKET_ENTRY_THEN_REDUCE_ONLY_STOP_AND_LIMIT_WITH_STRATEGY_SIDE_SIBLING_CANCEL"
)


class EasyChartRE1Strategy(EasyChartMTFStrategy):
    """EasyChart strategy with a Binance-compatible protective-order lifecycle."""

    def __init__(self, config: EasyChartMTFConfig) -> None:
        super().__init__(config)
        self.active_stop_id: ClientOrderId | None = None
        self.active_target_id: ClientOrderId | None = None
        self.protection_submitted = False
        self.position_closed_seen = False
        self.expected_cancel_ids: set[ClientOrderId] = set()
        self.cleanup_pending_ids: set[ClientOrderId] = set()

    def _protective_ids(self) -> tuple[ClientOrderId, ...]:
        return tuple(
            item
            for item in (self.active_stop_id, self.active_target_id)
            if item is not None
        )

    def _is_protective(self, client_order_id: ClientOrderId) -> bool:
        return client_order_id in self._protective_ids()

    def _reset_trade_state(self) -> None:
        self.active_plan = None
        self.active_instrument_id = None
        self.active_entry_id = None
        self.active_stop_id = None
        self.active_target_id = None
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False
        self.protection_submitted = False
        self.position_closed_seen = False
        self.cleanup_pending_ids.clear()

    def _finalize_when_clean(self) -> None:
        if not self.position_closed_seen:
            return
        remaining: set[ClientOrderId] = set()
        for client_order_id in self.cleanup_pending_ids:
            order = self.cache.order(client_order_id)
            if order is not None and not order.is_closed:
                remaining.add(client_order_id)
        self.cleanup_pending_ids = remaining
        if remaining:
            return
        plan_id = None if self.active_plan is None else self.active_plan.plan_id
        self._record("trade_slot_released_after_protective_cleanup", plan_id=plan_id)
        self._reset_trade_state()

    def _submit_plan(self, instrument_id: InstrumentId, plan: Any) -> bool:
        instrument = self.instruments[instrument_id]
        nav = self._current_nav()
        entry_slippage, stop_slippage = self._execution_reserves(instrument)
        quantity = self._quantity(instrument, plan, nav)
        if quantity is None:
            self._record(
                "plan_rejected_quantity",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                nav_at_submission=float(nav),
                estimated_entry_slippage=float(entry_slippage),
                estimated_stop_slippage=float(stop_slippage),
            )
            return False

        plan_tag = f"PLAN:{plan.plan_id}"
        entry = self.order_factory.market(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            reduce_only=False,
            tags=[plan_tag, "ROLE:ENTRY"],
        )
        self.active_plan = plan
        self.active_instrument_id = instrument_id
        self.active_entry_id = entry.client_order_id
        self.active_stop_id = None
        self.active_target_id = None
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False
        self.protection_submitted = False
        self.position_closed_seen = False
        self.expected_cancel_ids.clear()
        self.cleanup_pending_ids.clear()
        self.submit_order(entry)
        self._record(
            "submitted",
            plan_id=plan.plan_id,
            instrument_id=str(instrument_id),
            quantity=str(quantity),
            entry_client_order_id=str(self.active_entry_id),
            nav_at_submission=float(nav),
            risk_budget=float(nav * Decimal(str(self.config.risk_fraction))),
            estimated_entry_slippage=float(entry_slippage),
            estimated_stop_slippage=float(stop_slippage),
            execution_policy=LIVE_PROTECTION_POLICY,
        )
        return True

    def _submit_protectives(self, quantity: Any) -> None:
        if (
            self.active_plan is None
            or self.active_instrument_id is None
            or self.protection_submitted
            or self.emergency_exit_requested
        ):
            return
        instrument = self.instruments[self.active_instrument_id]
        plan = self.active_plan
        exit_side = OrderSide.SELL if plan.side is Side.LONG else OrderSide.BUY
        plan_tag = f"PLAN:{plan.plan_id}"
        try:
            stop = self.order_factory.stop_market(
                instrument_id=self.active_instrument_id,
                order_side=exit_side,
                quantity=quantity,
                trigger_price=instrument.make_price(plan.stop),
                trigger_type=TriggerType.DEFAULT,
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
                tags=[plan_tag, "ROLE:STOP_LOSS"],
            )
            target = self.order_factory.limit(
                instrument_id=self.active_instrument_id,
                order_side=exit_side,
                quantity=quantity,
                price=instrument.make_price(plan.target),
                time_in_force=TimeInForce.GTC,
                post_only=False,
                reduce_only=True,
                tags=[plan_tag, "ROLE:TAKE_PROFIT"],
            )
            self.active_stop_id = stop.client_order_id
            self.active_target_id = target.client_order_id
            self.protection_submitted = True
            # Stop first: if the second submission fails synchronously, the
            # position still has its invalidation order while emergency flatten
            # is requested.
            self.submit_order(stop)
            self.submit_order(target)
            self._record(
                "protectives_submitted",
                plan_id=plan.plan_id,
                instrument_id=str(self.active_instrument_id),
                quantity=str(quantity),
                stop_client_order_id=str(self.active_stop_id),
                target_client_order_id=str(self.active_target_id),
                stop=plan.stop,
                target=plan.target,
                execution_policy=LIVE_PROTECTION_POLICY,
            )
        except Exception as exc:
            self._record(
                "protective_submission_exception",
                plan_id=plan.plan_id,
                instrument_id=str(self.active_instrument_id),
                reason=repr(exc),
            )
            self._request_emergency_flatten("protective_submission_exception")

    @staticmethod
    def _decimal_quantity(value: Any) -> Decimal:
        return Decimal(str(value))

    def _sync_protective_quantities(self, open_quantity: Any) -> None:
        if (
            self.active_plan is None
            or self.active_instrument_id is None
            or not self.protection_submitted
            or self.emergency_exit_requested
        ):
            return
        desired_leaves = self._decimal_quantity(open_quantity)
        if desired_leaves <= 0:
            return
        instrument = self.instruments[self.active_instrument_id]
        step = Decimal(str(instrument.size_increment))
        for client_order_id in self._protective_ids():
            order = self.cache.order(client_order_id)
            if order is None or order.is_closed:
                continue
            leaves = self._decimal_quantity(order.leaves_qty)
            if abs(leaves - desired_leaves) < step:
                continue
            filled = self._decimal_quantity(order.filled_qty)
            desired_total = filled + desired_leaves
            try:
                self.modify_order(
                    order,
                    quantity=instrument.make_qty(desired_total),
                )
                self._record(
                    "protective_quantity_sync_requested",
                    plan_id=self.active_plan.plan_id,
                    client_order_id=str(client_order_id),
                    prior_leaves=str(order.leaves_qty),
                    desired_leaves=str(open_quantity),
                    desired_total=str(desired_total),
                )
            except Exception as exc:
                self._record(
                    "protective_quantity_sync_exception",
                    plan_id=self.active_plan.plan_id,
                    client_order_id=str(client_order_id),
                    reason=repr(exc),
                )
                self._request_emergency_flatten("protective_quantity_sync_exception")
                return

    def _request_emergency_flatten(self, reason: str) -> None:
        if (
            self.active_plan is None
            or self.active_instrument_id is None
            or self.emergency_exit_requested
        ):
            return
        self.emergency_exit_requested = True
        for client_order_id in self._protective_ids():
            self.expected_cancel_ids.add(client_order_id)
        self._record(
            "emergency_exit_protective_failure",
            plan_id=self.active_plan.plan_id,
            instrument_id=str(self.active_instrument_id),
            reason=reason,
        )
        self.cancel_all_orders(self.active_instrument_id)
        if not self.portfolio.is_flat(self.active_instrument_id):
            self.close_all_positions(self.active_instrument_id)

    def _clear_entry_without_position(self, client_order_id: ClientOrderId, reason: str) -> bool:
        if self.active_entry_id is None or client_order_id != self.active_entry_id:
            return False
        if self.active_instrument_id is not None and not self.portfolio.is_flat(self.active_instrument_id):
            return False
        plan_id = None if self.active_plan is None else self.active_plan.plan_id
        self._record(
            "entry_terminal_without_position",
            plan_id=plan_id,
            client_order_id=str(client_order_id),
            reason=reason,
        )
        self._reset_trade_state()
        return True

    def _protective_terminal(self, client_order_id: ClientOrderId, reason: str) -> None:
        if not self._is_protective(client_order_id):
            return
        if client_order_id in self.expected_cancel_ids or self.position_closed_seen:
            self.cleanup_pending_ids.discard(client_order_id)
            self.expected_cancel_ids.discard(client_order_id)
            self._record(
                "protective_cleanup_terminal",
                plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                client_order_id=str(client_order_id),
                reason=reason,
            )
            self._finalize_when_clean()
            return
        if self.active_instrument_id is not None and not self.portfolio.is_flat(self.active_instrument_id):
            self._request_emergency_flatten(f"{reason}:{client_order_id}")

    def on_position_opened(self, event: PositionOpened) -> None:
        if self.active_instrument_id is None or event.instrument_id != self.active_instrument_id:
            return
        self._record(
            "position_opened",
            plan_id=None if self.active_plan is None else self.active_plan.plan_id,
            instrument_id=str(event.instrument_id),
            position_id=str(event.position_id),
            quantity=str(event.quantity),
            avg_px_open=str(event.avg_px_open),
            event_ts_ns=event.ts_event,
        )
        self._submit_protectives(event.quantity)

    def on_position_changed(self, event: PositionChanged) -> None:
        if self.active_instrument_id is None or event.instrument_id != self.active_instrument_id:
            return
        self._record(
            "position_changed",
            plan_id=None if self.active_plan is None else self.active_plan.plan_id,
            instrument_id=str(event.instrument_id),
            position_id=str(event.position_id),
            quantity=str(event.quantity),
            last_qty=str(event.last_qty),
            last_px=str(event.last_px),
            event_ts_ns=event.ts_event,
        )
        self._sync_protective_quantities(event.quantity)

    def on_order_filled(self, event: OrderFilled) -> None:
        plan_id = self.active_plan.plan_id if self.active_plan else None
        if event.client_order_id == self.active_entry_id:
            role = "ENTRY"
        elif event.client_order_id == self.active_stop_id:
            role = "STOP_LOSS"
        elif event.client_order_id == self.active_target_id:
            role = "TAKE_PROFIT"
        else:
            role = "OTHER_OR_EMERGENCY_EXIT"
        self._record(
            "order_filled",
            plan_id=plan_id,
            role=role,
            client_order_id=str(event.client_order_id),
            venue_order_id=None if event.venue_order_id is None else str(event.venue_order_id),
            position_id=None if event.position_id is None else str(event.position_id),
            instrument_id=str(event.instrument_id),
            order_side=str(event.order_side),
            order_type=str(event.order_type),
            last_qty=str(event.last_qty),
            last_px=str(event.last_px),
            commission=str(event.commission),
            liquidity_side=str(event.liquidity_side),
            event_ts_ns=event.ts_event,
        )

    def on_order_accepted(self, event: OrderAccepted) -> None:
        if self.active_plan is None:
            return
        role = (
            "ENTRY"
            if event.client_order_id == self.active_entry_id
            else "PROTECTIVE"
            if self._is_protective(event.client_order_id)
            else "OTHER"
        )
        self._record(
            "order_accepted",
            plan_id=self.active_plan.plan_id,
            role=role,
            client_order_id=str(event.client_order_id),
            venue_order_id=None if event.venue_order_id is None else str(event.venue_order_id),
        )

    def on_order_updated(self, event: OrderUpdated) -> None:
        if self._is_protective(event.client_order_id):
            self._record(
                "protective_order_updated",
                plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                client_order_id=str(event.client_order_id),
                quantity=str(event.quantity),
            )

    def on_order_canceled(self, event: OrderCanceled) -> None:
        if self._clear_entry_without_position(event.client_order_id, "canceled"):
            return
        self._protective_terminal(event.client_order_id, "canceled")

    def on_order_expired(self, event: OrderExpired) -> None:
        if self._clear_entry_without_position(event.client_order_id, "expired"):
            return
        self._protective_terminal(event.client_order_id, "expired")

    def on_order_rejected(self, event: OrderRejected) -> None:
        reason = str(event.reason)
        self._record("order_rejected", client_order_id=str(event.client_order_id), reason=reason)
        if self._clear_entry_without_position(event.client_order_id, f"rejected:{reason}"):
            return
        self._protective_terminal(event.client_order_id, f"rejected:{reason}")

    def on_order_denied(self, event: OrderDenied) -> None:
        reason = str(event.reason)
        self._record("order_denied", client_order_id=str(event.client_order_id), reason=reason)
        if self._clear_entry_without_position(event.client_order_id, f"denied:{reason}"):
            return
        self._protective_terminal(event.client_order_id, f"denied:{reason}")

    def on_order_modify_rejected(self, event: OrderModifyRejected) -> None:
        reason = str(event.reason)
        self._record(
            "order_modify_rejected",
            client_order_id=str(event.client_order_id),
            reason=reason,
        )
        if self._is_protective(event.client_order_id):
            self._request_emergency_flatten(f"modify_rejected:{reason}")

    def on_order_cancel_rejected(self, event: OrderCancelRejected) -> None:
        reason = str(event.reason)
        self._record(
            "order_cancel_rejected",
            client_order_id=str(event.client_order_id),
            reason=reason,
        )
        if not self._is_protective(event.client_order_id):
            return
        if self.active_instrument_id is not None and not self.portfolio.is_flat(self.active_instrument_id):
            self._request_emergency_flatten(f"cancel_rejected:{reason}")
        elif self.active_instrument_id is not None:
            # A flat account is safe, but do not release the slot while a stale
            # reduce-only sibling may still exist. Reconciliation or a later
            # terminal event must close it.
            self.cancel_all_orders(self.active_instrument_id)

    def on_position_closed(self, event: PositionClosed) -> None:
        if self.active_instrument_id is None or event.instrument_id != self.active_instrument_id:
            return
        plan_id = self.active_plan.plan_id if self.active_plan else None
        self.position_closed_seen = True
        self._record(
            "position_closed",
            plan_id=plan_id,
            instrument_id=str(event.instrument_id),
            position_id=str(event.position_id),
            realized_pnl=str(event.realized_pnl),
            event_ts_ns=event.ts_event,
        )
        pending: set[ClientOrderId] = set()
        for client_order_id in self._protective_ids():
            order = self.cache.order(client_order_id)
            if order is None or order.is_closed:
                continue
            pending.add(client_order_id)
            self.expected_cancel_ids.add(client_order_id)
            try:
                self.cancel_order(order)
            except Exception as exc:
                self._record(
                    "protective_cleanup_cancel_exception",
                    plan_id=plan_id,
                    client_order_id=str(client_order_id),
                    reason=repr(exc),
                )
                self.cancel_all_orders(event.instrument_id)
        self.cleanup_pending_ids = pending
        self._finalize_when_clean()


__all__ = [
    "EasyChartMTFConfig",
    "EasyChartRE1Strategy",
    "LIVE_PROTECTION_POLICY",
]
