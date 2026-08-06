"""Controlled parent-only execution lifecycle for TradeTick research.

The preceding bracket implementation allowed the parent LIMIT to continue
partially filling while a child STOP_MARKET was rejected during contingent-order
activation. This module changes no signal, price, target, stop, size, fee, risk
or fill assumption. It submits only the passive parent first, cancels its
unfilled remainder after the first execution, and protects every actual fill
quantity with independent reduce-only exit orders owned by NautilusTrader.
"""

from __future__ import annotations

from typing import Any

import c10_flow_research as _research_module
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TriggerType

from smc_ict_4.contracts import ResearchEvent

from c10_flow_model import FlowTickView
from c10_flow_model import FlowTradePlan
from c10_flow_precision_fix import run_flow_backtest as _precision_run_flow_backtest
from c10_flow_strategy import FlowCandidate10Strategy


PROTECTIVE_STOP_TRIGGER_TYPE = TriggerType.LAST_PRICE


def protective_action(
    *,
    direction: int,
    last_price: float,
    stop_price: float,
    target_price: float,
) -> str:
    """Return the executable action for a newly filled parent quantity."""

    if direction > 0:
        if last_price <= stop_price:
            return "MARKET_STOP"
        if last_price >= target_price:
            return "MARKET_TARGET"
    else:
        if last_price >= stop_price:
            return "MARKET_STOP"
        if last_price <= target_price:
            return "MARKET_TARGET"
    return "RESTING_STOP_TARGET"


class ParentProtectedFlowCandidate10Strategy(FlowCandidate10Strategy):
    """Cancel remainder after first fill and protect each executed chunk."""

    def __init__(self, config: Any):
        super().__init__(config)
        self.parent_order_client_id: Any | None = None
        self.parent_cancel_requested = False
        self.exit_mates: dict[Any, Any] = {}
        self.exit_roles: dict[Any, str] = {}
        self.last_trade_price: float | None = None
        self.actual_parent_filled_qty = 0.0
        self.protected_chunk_count = 0
        self.emergency_flatten_pending = False
        self.emergency_flatten_reason: str | None = None

    @staticmethod
    def _order_open(order: Any) -> bool:
        value = getattr(order, "is_open", False)
        return bool(value() if callable(value) else value)

    def _cancel_order_id(self, client_order_id: Any | None) -> None:
        if client_order_id is None:
            return
        order = self.cache.order(client_order_id)
        if order is not None and self._order_open(order):
            self.cancel_order(order)

    def _cancel_parent_remainder(self) -> None:
        if self.parent_cancel_requested:
            return
        self.parent_cancel_requested = True
        self._cancel_order_id(self.parent_order_client_id)

    def _reset_parent_execution(self) -> None:
        self.parent_order_client_id = None
        self.parent_cancel_requested = False
        self.exit_mates.clear()
        self.exit_roles.clear()
        self.actual_parent_filled_qty = 0.0
        self.protected_chunk_count = 0
        self.emergency_flatten_pending = False
        self.emergency_flatten_reason = None

    def _append_execution_event(
        self,
        *,
        event_type: str,
        reason_code: str,
        ts_ns: int,
        previous_state: str,
        next_state: str,
        reference_price: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.active_trade is None:
            return
        self.events.append(
            ResearchEvent(
                scenario_id=str(self.active_trade["scenario_id"]),
                instrument_id=str(self.config.instrument_id),
                event_type=event_type,
                event_time_ns=ts_ns,
                observed_time_ns=ts_ns,
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=(
                    None if reference_price is None else str(reference_price)
                ),
                details=dict(details or {}),
            ),
        )

    def _submit_flow_plan(self, plan: FlowTradePlan, view: FlowTickView) -> None:
        assert self.instrument is not None
        if plan.direction > 0:
            entry = self._round_price(plan.entry_price, upward=False)
            stop = self._round_price(plan.stop_price, upward=False)
            target = self._round_price(plan.target_price, upward=False)
            valid = stop.as_double() < entry.as_double() < target.as_double()
            side = OrderSide.BUY
        else:
            entry = self._round_price(plan.entry_price, upward=True)
            stop = self._round_price(plan.stop_price, upward=True)
            target = self._round_price(plan.target_price, upward=True)
            valid = target.as_double() < entry.as_double() < stop.as_double()
            side = OrderSide.SELL
        if not valid:
            return

        quantity = self._risk_quantity(  # type: ignore[arg-type]
            plan,
            entry.as_double(),
            stop.as_double(),
        )
        if quantity is None:
            return

        parent = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            price=entry,
            post_only=True,
            reduce_only=False,
            tags=["ENTRY_PARENT_ONLY"],
        )
        start_equity = self._equity()
        current_sequence = (
            self.flow_machine.completed_sequence
            if self.flow_machine is not None
            else 0
        )
        self._reset_parent_execution()
        self.parent_order_client_id = parent.client_order_id
        self.entry_pending = True
        self.pending_expiry_sequence = current_sequence + plan.entry_expiry_bars
        self.pending_expiry_ns = None
        self.pending_invalidation_price = stop.as_double()
        self.pending_direction = plan.direction
        self.orders_submitted += 1
        self.active_trade = {
            "scenario_id": plan.scenario_id,
            "scenario": plan.scenario,
            "direction": plan.direction,
            "signal_ts_ns": view.ts_ns,
            "entry_estimate": entry.as_double(),
            "stop": stop.as_double(),
            "target": target.as_double(),
            "quantity": 0.0,
            "planned_quantity": quantity.as_double(),
            "start_equity": start_equity,
            "structural_target": "OPPOSITE_EVENT_RANGE_BOUNDARY",
            "entry_order_type": "LIMIT_POST_ONLY_PARENT_ONLY",
            "planned_expiry_sequence": self.pending_expiry_sequence,
            "event_atr": plan.event_atr,
            "source_boundary": plan.source_boundary,
            "opposite_boundary": plan.opposite_boundary,
            "flow_details": dict(plan.details),
            "parent_client_order_id": str(parent.client_order_id),
            "parent_remainder_policy": "CANCEL_AFTER_FIRST_EXECUTION",
            "protection_policy": "PER_FILL_REDUCE_ONLY_STOP_TARGET",
            "event_state": "ORDER_PENDING",
        }
        self._append_execution_event(
            event_type="ORDER_SUBMITTED",
            reason_code="NAUTILUS_PARENT_ONLY_POST_ONLY_LIMIT_SUBMITTED",
            ts_ns=view.ts_ns,
            previous_state="ENTRY_READY",
            next_state="ORDER_PENDING",
            reference_price=entry.as_double(),
            details={
                "planned_quantity": quantity.as_double(),
                "entry": entry.as_double(),
                "stop": stop.as_double(),
                "target": target.as_double(),
                "risk_fraction": str(self.config.risk_fraction),
                "expiry_sequence": self.pending_expiry_sequence,
                "parent_client_order_id": str(parent.client_order_id),
            },
        )
        self.submit_order(parent)

    def _cancel_pending(
        self,
        ts_ns: int,
        reason: str,
        reference_price: float | None,
    ) -> None:
        if not self.entry_pending:
            return
        self._cancel_parent_remainder()
        self.pending_cancellations += 1
        self._append_execution_event(
            event_type="ORDER_CANCELED",
            reason_code=reason,
            ts_ns=ts_ns,
            previous_state="ORDER_PENDING",
            next_state="CANCELED",
            reference_price=reference_price,
            details={
                "expiry_sequence": self.pending_expiry_sequence,
                "invalidation_price": self.pending_invalidation_price,
                "actual_parent_filled_qty": self.actual_parent_filled_qty,
            },
        )
        if self.portfolio.is_flat(self.config.instrument_id):
            self.active_trade = None
            self.parent_order_client_id = None
        self._clear_pending_fields()

    def _register_exit(self, order: Any, role: str) -> None:
        self.exit_roles[order.client_order_id] = role

    def _register_pair(self, first: Any, second: Any) -> None:
        self.exit_mates[first.client_order_id] = second.client_order_id
        self.exit_mates[second.client_order_id] = first.client_order_id

    def _submit_market_exit(
        self,
        *,
        quantity: Any,
        side: Any,
        role: str,
        ts_ns: int,
        last_price: float,
    ) -> None:
        market = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            reduce_only=True,
            tags=[role],
        )
        self._register_exit(market, role)
        self._append_execution_event(
            event_type="PROTECTION_SUBMITTED",
            reason_code=role,
            ts_ns=ts_ns,
            previous_state="PARENT_PARTIAL_FILL",
            next_state="PROTECTIVE_MARKET_PENDING",
            reference_price=last_price,
            details={
                "quantity": quantity.as_double(),
                "stop": self.active_trade["stop"] if self.active_trade else None,
                "target": self.active_trade["target"] if self.active_trade else None,
            },
        )
        self.submit_order(market)

    def _submit_protection_for_fill(self, event: Any) -> None:
        if self.active_trade is None:
            return
        quantity = event.last_qty
        direction = int(self.active_trade["direction"])
        stop_value = float(self.active_trade["stop"])
        target_value = float(self.active_trade["target"])
        last_price = (
            self.last_trade_price
            if self.last_trade_price is not None
            else event.last_px.as_double()
        )
        exit_side = OrderSide.SELL if direction > 0 else OrderSide.BUY
        action = protective_action(
            direction=direction,
            last_price=last_price,
            stop_price=stop_value,
            target_price=target_value,
        )
        ts_ns = int(getattr(event, "ts_event", self.clock.timestamp_ns()))
        if action == "MARKET_STOP":
            self._submit_market_exit(
                quantity=quantity,
                side=exit_side,
                role="PROTECTIVE_STOP_GAP_MARKET",
                ts_ns=ts_ns,
                last_price=last_price,
            )
            return
        if action == "MARKET_TARGET":
            self._submit_market_exit(
                quantity=quantity,
                side=exit_side,
                role="PROTECTIVE_TARGET_GAP_MARKET",
                ts_ns=ts_ns,
                last_price=last_price,
            )
            return

        assert self.instrument is not None
        stop = self.instrument.make_price(stop_value)
        target = self.instrument.make_price(target_value)
        stop_order = self.order_factory.stop_market(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=quantity,
            trigger_price=stop,
            trigger_type=PROTECTIVE_STOP_TRIGGER_TYPE,
            reduce_only=True,
            tags=["PROTECTIVE_STOP_LAST_PRICE"],
        )
        target_order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=quantity,
            price=target,
            post_only=True,
            reduce_only=True,
            tags=["PROTECTIVE_TARGET_POST_ONLY"],
        )
        self._register_exit(stop_order, "PROTECTIVE_STOP")
        self._register_exit(target_order, "PROTECTIVE_TARGET")
        self._register_pair(stop_order, target_order)
        self.protected_chunk_count += 1
        self._append_execution_event(
            event_type="PROTECTION_SUBMITTED",
            reason_code="PER_FILL_REDUCE_ONLY_STOP_TARGET_SUBMITTED",
            ts_ns=ts_ns,
            previous_state="PARENT_PARTIAL_FILL",
            next_state="POSITION_PROTECTED",
            reference_price=last_price,
            details={
                "quantity": quantity.as_double(),
                "stop": stop_value,
                "target": target_value,
                "stop_trigger_type": str(PROTECTIVE_STOP_TRIGGER_TYPE),
                "stop_client_order_id": str(stop_order.client_order_id),
                "target_client_order_id": str(target_order.client_order_id),
                "protected_chunk_count": self.protected_chunk_count,
            },
        )
        self.submit_order(stop_order)
        if not self.emergency_flatten_pending:
            self.submit_order(target_order)

    def on_order_filled(self, event: Any) -> None:
        client_order_id = event.client_order_id
        if client_order_id == self.parent_order_client_id:
            fill_qty = event.last_qty.as_double()
            self.actual_parent_filled_qty += fill_qty
            if self.active_trade is not None:
                self.active_trade["quantity"] = self.actual_parent_filled_qty
                self.active_trade["actual_parent_filled_qty"] = (
                    self.actual_parent_filled_qty
                )
            self._cancel_parent_remainder()
            self._submit_protection_for_fill(event)
            return

        role = self.exit_roles.get(client_order_id)
        if role is None:
            return
        mate = self.exit_mates.pop(client_order_id, None)
        if mate is not None:
            self.exit_mates.pop(mate, None)
            self._cancel_order_id(mate)
            self.exit_roles.pop(mate, None)
        self.exit_roles.pop(client_order_id, None)
        if self.active_trade is not None:
            self.active_trade["last_exit_role"] = role

    def on_position_opened(self, event: Any) -> None:
        if self.active_trade is not None:
            self.active_trade["quantity"] = self.actual_parent_filled_qty
        super().on_position_opened(event)

    def on_position_closed(self, event: Any) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        super().on_position_closed(event)
        self._reset_parent_execution()

    def _handle_parent_execution_error(self, event: Any, kind: str) -> None:
        ts_ns = int(getattr(event, "ts_event", self.clock.timestamp_ns()))
        client_order_id = getattr(event, "client_order_id", None)
        role = self.exit_roles.get(client_order_id)
        payload = {
            "kind": kind,
            "ts_ns": ts_ns,
            "client_order_id": (
                None if client_order_id is None else str(client_order_id)
            ),
            "role": role,
            "event": str(event),
        }
        self.order_errors.append(payload)
        self._append_execution_event(
            event_type="ORDER_ERROR",
            reason_code=kind,
            ts_ns=ts_ns,
            previous_state=(
                "ORDER_PENDING" if role is None else "POSITION_PROTECTED"
            ),
            next_state="ORDER_ERROR",
            details=payload,
        )

        if client_order_id == self.parent_order_client_id:
            self._clear_pending_fields()
            self.parent_order_client_id = None
            if self.portfolio.is_flat(self.config.instrument_id):
                self.active_trade = None
                return

        mate = self.exit_mates.pop(client_order_id, None)
        if mate is not None:
            self.exit_mates.pop(mate, None)
            self._cancel_order_id(mate)
            self.exit_roles.pop(mate, None)
        self.exit_roles.pop(client_order_id, None)
        self._cancel_parent_remainder()
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.emergency_flatten_pending = True
            self.emergency_flatten_reason = f"{kind}:{role or 'UNKNOWN_ORDER'}"
        elif self.active_trade is not None:
            self.active_trade = None

    def on_order_denied(self, event: Any) -> None:
        self._handle_parent_execution_error(event, "DENIED")

    def on_order_rejected(self, event: Any) -> None:
        self._handle_parent_execution_error(event, "REJECTED")

    def _execute_emergency_flatten(self, ts_ns: int) -> bool:
        if not self.emergency_flatten_pending:
            return False
        self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            reason = self.emergency_flatten_reason or "PROTECTIVE_ORDER_ERROR"
            self._append_execution_event(
                event_type="EMERGENCY_FLATTEN_SUBMITTED",
                reason_code=reason,
                ts_ns=ts_ns,
                previous_state="ORDER_ERROR",
                next_state="EMERGENCY_EXIT_PENDING",
                reference_price=self.last_trade_price,
                details={
                    "actual_parent_filled_qty": self.actual_parent_filled_qty,
                },
            )
            self.close_all_positions(self.config.instrument_id)
            self.forced_exits += 1
        else:
            self.active_trade = None
        self.emergency_flatten_pending = False
        self.emergency_flatten_reason = None
        return True

    def on_trade_tick(self, tick: Any) -> None:
        self.last_trade_price = tick.price.as_double()
        if self._execute_emergency_flatten(int(tick.ts_event)):
            return
        super().on_trade_tick(tick)

    def _force_flat(self, ts_ns: int) -> None:
        if self.entry_pending:
            self._cancel_pending(ts_ns, "SCHEDULED_FLAT_WINDOW", None)
        self._cancel_parent_remainder()
        self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id)
            self.forced_exits += 1
        elif self.active_trade is not None and self.actual_parent_filled_qty <= 0.0:
            self.active_trade = None
            self._reset_parent_execution()


def run_parent_protected_backtest(**kwargs: Any) -> dict[str, Any]:
    """Patch only the strategy execution lifecycle for a controlled rerun."""

    previous = _research_module.FlowCandidate10Strategy
    _research_module.FlowCandidate10Strategy = ParentProtectedFlowCandidate10Strategy
    try:
        metrics = _precision_run_flow_backtest(**kwargs)
    finally:
        _research_module.FlowCandidate10Strategy = previous
    metrics["execution_lifecycle"] = (
        "PARENT_ONLY_CANCEL_REMAINDER_PER_FILL_REDUCE_ONLY_PROTECTION"
    )
    metrics["protective_stop_trigger_type"] = str(PROTECTIVE_STOP_TRIGGER_TYPE)
    return metrics


__all__ = [
    "PROTECTIVE_STOP_TRIGGER_TYPE",
    "ParentProtectedFlowCandidate10Strategy",
    "protective_action",
    "run_parent_protected_backtest",
]
