"""NautilusTrader limit-entry binding for EasyChart v3."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import mtf_strategy as _base
from limit_scenario_v3 import LimitResearchScenarioBundle
from model import Side
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce, TriggerType
from nautilus_trader.model.events import (
    OrderCanceled,
    OrderDenied,
    OrderExpired,
    OrderFilled,
    OrderRejected,
    PositionClosed,
)
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.orders.list import OrderList

_base.MultiScaleScenarioBundle = LimitResearchScenarioBundle
EasyChartMTFConfig = _base.EasyChartMTFConfig


class EasyChartMTFStrategy(_base.EasyChartMTFStrategy):
    """One global slot; one planned GTC limit with causally linked protection.

    NautilusTrader's backtest venue uses partial-trigger OTO semantics.  A parent
    limit can therefore fill in pieces while the proportional stop/target child
    orders are already live.  Once any protective child starts executing, the
    remaining parent quantity must be canceled: allowing it to fill later would
    reopen the same causal episode after its exit and can leave the new quantity
    without live protection.
    """

    def __init__(self, config: EasyChartMTFConfig) -> None:
        super().__init__(config)
        self._last_execution_bar: dict[InstrumentId, Bar] = {}
        self._entry_fill_seen = False
        self._exit_fill_seen = False
        self._position_closed_seen = False
        self._entry_cancel_reason: str | None = None

    def _reset_trade_lifecycle(self) -> None:
        self._entry_fill_seen = False
        self._exit_fill_seen = False
        self._position_closed_seen = False
        self._entry_cancel_reason = None

    def _clear_active_trade(self, reason: str) -> None:
        plan_id = None if self.active_plan is None else self.active_plan.plan_id
        instrument_id = self.active_instrument_id
        self._record(
            "active_trade_cleared",
            plan_id=plan_id,
            instrument_id=None if instrument_id is None else str(instrument_id),
            reason=reason,
        )
        self.active_plan = None
        self.active_instrument_id = None
        self.active_entry_id = None
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False
        self._reset_trade_lifecycle()

    def _maybe_clear_active_trade(self, reason: str) -> bool:
        if self.active_plan is None or self.active_instrument_id is None:
            return False
        entry = None if self.active_entry_id is None else self.cache.order(self.active_entry_id)
        entry_terminal = entry is None or entry.is_closed
        portfolio_flat = self.portfolio.is_flat(self.active_instrument_id)
        if not entry_terminal or not portfolio_flat:
            return False
        # A real fill must complete its position lifecycle before the global
        # slot is released.  This prevents a new plan from racing an in-flight
        # PositionClosed event or a parent-cancel acknowledgement.
        if self._entry_fill_seen and not self._position_closed_seen:
            return False
        self._clear_active_trade(reason)
        return True

    def _cancel_open_entry_remainder(self, reason: str, event_ts_ns: int | None = None) -> bool:
        if (
            self.active_plan is None
            or self.active_instrument_id is None
            or self.active_entry_id is None
            or self.entry_cancel_requested
        ):
            return False
        order = self.cache.order(self.active_entry_id)
        if order is None or not order.is_open:
            return False
        self.entry_cancel_requested = True
        self._entry_cancel_reason = reason
        self.cancel_order(order)
        self._record(
            "remaining_entry_cancel_requested",
            plan_id=self.active_plan.plan_id,
            instrument_id=str(self.active_instrument_id),
            client_order_id=str(self.active_entry_id),
            reason=reason,
            event_ts_ns=event_ts_ns,
            filled_qty=str(order.filled_qty),
            total_qty=str(order.quantity),
        )
        return True

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
        order_list: OrderList = self.order_factory.bracket(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            entry_order_type=OrderType.LIMIT,
            entry_price=instrument.make_price(plan.entry),
            entry_post_only=False,
            sl_trigger_price=instrument.make_price(plan.stop),
            tp_price=instrument.make_price(plan.target),
            tp_post_only=False,
            emulation_trigger=TriggerType.NO_TRIGGER,
            entry_tags=[plan_tag, "ROLE:ENTRY", "POLICY:PLANNED_ZONE_LIMIT"],
            sl_tags=[plan_tag, "ROLE:STOP_LOSS"],
            tp_tags=[plan_tag, "ROLE:TAKE_PROFIT"],
        )
        self.active_plan = plan
        self.active_instrument_id = instrument_id
        self.active_entry_id = order_list.first.client_order_id
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False
        self._reset_trade_lifecycle()
        self.submit_order_list(order_list)
        self._record(
            "submitted",
            plan_id=plan.plan_id,
            instrument_id=str(instrument_id),
            quantity=str(quantity),
            entry_client_order_id=str(self.active_entry_id),
            planned_entry=plan.entry,
            entry_policy="PLANNED_ZONE_LIMIT_GTC",
            nav_at_submission=float(nav),
            risk_budget=float(nav * Decimal(str(self.config.risk_fraction))),
            estimated_entry_slippage=float(entry_slippage),
            estimated_stop_slippage=float(stop_slippage),
        )
        return True

    def _cancel_resolved_unfilled_parent(self, instrument_id: InstrumentId, previous: Bar) -> None:
        if (
            self.active_plan is None
            or self.active_instrument_id != instrument_id
            or self.active_entry_id is None
            or self.entry_cancel_requested
        ):
            return
        order = self.cache.order(self.active_entry_id)
        if order is None or not order.is_open:
            return
        # Once any quantity has become a real position, the native bracket owns
        # protection. Never cancel a partially filled parent as if it were still
        # a purely hypothetical setup. The remainder is canceled only when an
        # exit starts or the position closes.
        if not self.portfolio.is_flat(instrument_id):
            return
        plan = self.active_plan
        if previous.ts_event <= plan.observed_time_ns:
            return
        if plan.side is Side.LONG:
            invalidated = float(previous.low) <= plan.stop
            objective_spent = float(previous.high) >= plan.target
        else:
            invalidated = float(previous.high) >= plan.stop
            objective_spent = float(previous.low) <= plan.target
        if not (invalidated or objective_spent):
            return
        reason = "PRE_ENTRY_INVALIDATION" if invalidated else "PRE_ENTRY_TARGET_SPENT"
        self._cancel_open_entry_remainder(reason, previous.ts_event)

    def on_order_filled(self, event: OrderFilled) -> None:
        plan_id = None if self.active_plan is None else self.active_plan.plan_id
        is_entry = self.active_entry_id is not None and event.client_order_id == self.active_entry_id
        role = "ENTRY" if is_entry else "EXIT_OR_PROTECTIVE"

        if is_entry:
            if self._exit_fill_seen:
                # This is the exact invalid state which previously produced a
                # second unprotected position after a partial take-profit.
                self._record(
                    "entry_fill_after_exit",
                    plan_id=plan_id,
                    client_order_id=str(event.client_order_id),
                    instrument_id=str(event.instrument_id),
                    event_ts_ns=event.ts_event,
                    last_qty=str(event.last_qty),
                    last_px=str(event.last_px),
                )
                self._cancel_open_entry_remainder("ENTRY_FILL_AFTER_EXIT", event.ts_event)
                if not self.portfolio.is_flat(event.instrument_id) and not self.emergency_exit_requested:
                    self.emergency_exit_requested = True
                    self.cancel_all_orders(event.instrument_id)
                    self.close_all_positions(event.instrument_id)
            self._entry_fill_seen = True
        elif self.active_plan is not None:
            self._exit_fill_seen = True
            # A stop/target execution resolves the causal episode for every
            # still-unfilled parent unit. Re-entering later at the old limit is
            # neither the same trade nor an independent new opportunity.
            self._cancel_open_entry_remainder("EXIT_STARTED", event.ts_event)

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
        self._maybe_clear_active_trade("ORDER_FILLED_TERMINAL")

    def on_order_canceled(self, event: OrderCanceled) -> None:
        is_entry = self.active_entry_id is not None and event.client_order_id == self.active_entry_id
        self._record(
            "order_canceled",
            plan_id=None if self.active_plan is None else self.active_plan.plan_id,
            client_order_id=str(event.client_order_id),
            role="ENTRY" if is_entry else "PROTECTIVE",
            cancel_reason=self._entry_cancel_reason if is_entry else None,
        )
        self._maybe_clear_active_trade("ENTRY_PARENT_CANCELED" if is_entry else "PROTECTIVE_CANCELED")

    def on_order_expired(self, event: OrderExpired) -> None:
        is_entry = self.active_entry_id is not None and event.client_order_id == self.active_entry_id
        self._record(
            "order_expired",
            plan_id=None if self.active_plan is None else self.active_plan.plan_id,
            client_order_id=str(event.client_order_id),
            role="ENTRY" if is_entry else "PROTECTIVE",
        )
        self._maybe_clear_active_trade("ENTRY_PARENT_EXPIRED" if is_entry else "PROTECTIVE_EXPIRED")

    def on_order_rejected(self, event: OrderRejected) -> None:
        reason = str(event.reason)
        is_entry = self.active_entry_id is not None and event.client_order_id == self.active_entry_id
        self._record(
            "order_rejected",
            plan_id=None if self.active_plan is None else self.active_plan.plan_id,
            client_order_id=str(event.client_order_id),
            role="ENTRY" if is_entry else "PROTECTIVE",
            reason=reason,
        )
        if is_entry:
            self._maybe_clear_active_trade("ENTRY_PARENT_REJECTED")
            return
        self._cancel_open_entry_remainder("PROTECTIVE_REJECTED", event.ts_event)
        self._protective_failure(event.client_order_id, reason)

    def on_order_denied(self, event: OrderDenied) -> None:
        reason = str(event.reason)
        is_entry = self.active_entry_id is not None and event.client_order_id == self.active_entry_id
        self._record(
            "order_denied",
            plan_id=None if self.active_plan is None else self.active_plan.plan_id,
            client_order_id=str(event.client_order_id),
            role="ENTRY" if is_entry else "PROTECTIVE",
            reason=reason,
        )
        if is_entry:
            self._maybe_clear_active_trade("ENTRY_PARENT_DENIED")
            return
        self._cancel_open_entry_remainder("PROTECTIVE_DENIED", event.ts_event)
        self._protective_failure(event.client_order_id, reason)

    def on_position_closed(self, event: PositionClosed) -> None:
        plan_id = None if self.active_plan is None else self.active_plan.plan_id
        self._position_closed_seen = True
        self._record("position_closed", plan_id=plan_id, instrument_id=str(event.instrument_id))
        # If a proportional stop/target closes the currently filled quantity
        # while the parent still has remainder, terminate that remainder now.
        self._cancel_open_entry_remainder("POSITION_CLOSED", event.ts_event)
        self._maybe_clear_active_trade("POSITION_AND_ENTRY_TERMINAL")

    def on_bar(self, bar: Bar) -> None:
        route = self.route_by_key.get(bar.bar_type.id_spec_key())
        if route is not None:
            instrument_id, timeframe = route
            if timeframe == self.EXECUTION_MINUTES:
                previous = self._last_execution_bar.get(instrument_id)
                if previous is not None:
                    # Evaluate the fully completed previous minute. This avoids
                    # assuming whether the matching engine processes a pending
                    # order before or after the strategy callback for this bar.
                    self._cancel_resolved_unfilled_parent(instrument_id, previous)
                self._last_execution_bar[instrument_id] = bar
        super().on_bar(bar)
