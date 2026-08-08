"""Fail-closed materialization of a self-financing first-delivery FAR exit tree.

The inherited detector, admission, entry price, initial stop, external target,
current-NAV 3% sizing and global one-slot rule are unchanged.  A single parent
entry remains the only pending new-entry order.  After real Nautilus fills, the
strategy submits two reduce-only limits and one reduce-only stop:

* a causal first-delivery primary quantity;
* an external-target runner quantity; and
* one stop protecting the full remaining position.

NautilusTrader owns every order, partial fill, fee, modification, position and
NAV transition.  Any missing price node, infeasible exchange rounding, child
submission/modify rejection or inconsistent fill order fails closed.
"""
from __future__ import annotations

from runner_materializer import NEW_ORDER_BLOCK


POLICY = "SELF_FINANCING_FIRST_DELIVERY_EXTERNAL_RUNNER"


def _replace(
    source: str,
    old: str,
    new: str,
    *,
    label: str,
    expected: int = 1,
) -> str:
    count = source.count(old)
    if count != expected:
        raise RuntimeError(
            f"first-delivery materialization drifted at {label}: "
            f"expected {expected} occurrence(s), found {count}",
        )
    return source.replace(old, new)


def materialize_first_delivery_source(source: str) -> str:
    source = _replace(
        source,
        '''            self.active_position_opened_ns: int | None = None
            self.time_exit_requested = False
            self.resolution_exit_requested = False
            self.last_ts_ns = 0''',
        '''            self.active_position_opened_ns: int | None = None
            self.time_exit_requested = False
            self.resolution_exit_requested = False
            self.split_entry_order_id = None
            self.split_stop_order_id = None
            self.split_primary_order_id = None
            self.split_runner_order_id = None
            self.split_total_qty = Decimal("0")
            self.split_primary_qty = Decimal("0")
            self.split_runner_qty = Decimal("0")
            self.split_entry_filled_qty = Decimal("0")
            self.split_primary_filled_qty = Decimal("0")
            self.split_runner_filled_qty = Decimal("0")
            self.split_stop_filled_qty = Decimal("0")
            self.split_targets_submitted = False
            self.split_enabled = False
            self.split_fail_closed = False
            self.last_ts_ns = 0''',
        label="first-delivery-state",
    )

    source = _replace(
        source,
        '''        def _release_if_terminal(self, ts_ns: int, reason: str) -> None:''',
        '''        def _reset_first_delivery_state(self) -> None:
            self.split_entry_order_id = None
            self.split_stop_order_id = None
            self.split_primary_order_id = None
            self.split_runner_order_id = None
            self.split_total_qty = Decimal("0")
            self.split_primary_qty = Decimal("0")
            self.split_runner_qty = Decimal("0")
            self.split_entry_filled_qty = Decimal("0")
            self.split_primary_filled_qty = Decimal("0")
            self.split_runner_filled_qty = Decimal("0")
            self.split_stop_filled_qty = Decimal("0")
            self.split_targets_submitted = False
            self.split_enabled = False
            self.split_fail_closed = False

        def _split_order(self, client_order_id):
            if client_order_id is None:
                return None
            return self.cache.order(client_order_id)

        def _cancel_split_order(self, client_order_id) -> None:
            order = self._split_order(client_order_id)
            if order is not None and not order.is_closed:
                self.cancel_order(order)

        def _first_delivery_fail_close(self, reason: str, details=None) -> None:
            if self.split_fail_closed:
                return
            self.split_fail_closed = True
            record = {
                "type": "FIRST_DELIVERY_FAIL_CLOSED",
                "ts_event": self.last_ts_ns,
                "scenario_id": (
                    None if self.active_plan is None else self.active_plan.scenario_id
                ),
                "symbol": self.active_symbol,
                "reason": reason,
                "details": details or {},
            }
            self.lifecycle.append(record)
            self.errors.append(record)
            if self.active_symbol is None:
                return
            instrument_id = instruments[self.active_symbol].id
            if self.cache.orders_open_count(
                instrument_id=instrument_id,
                strategy_id=self.id,
            ):
                self.cancel_all_orders(instrument_id)
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id)

        def _remaining_split_qty(self) -> Decimal:
            remaining = (
                self.split_entry_filled_qty
                - self.split_primary_filled_qty
                - self.split_runner_filled_qty
                - self.split_stop_filled_qty
            )
            return max(Decimal("0"), remaining)

        def _ensure_first_delivery_stop(self, quantity: Decimal) -> None:
            if (
                quantity <= 0
                or self.active_plan is None
                or self.active_symbol is None
                or self.split_fail_closed
            ):
                return
            instrument = instruments[self.active_symbol]
            existing = self._split_order(self.split_stop_order_id)
            try:
                if existing is None:
                    exit_side = (
                        OrderSide.SELL
                        if self.active_plan.direction == Direction.LONG
                        else OrderSide.BUY
                    )
                    stop = self.order_factory.stop_market(
                        instrument_id=instrument.id,
                        order_side=exit_side,
                        quantity=instrument.make_qty(quantity),
                        trigger_price=instrument.make_price(
                            self.active_plan.stop_price
                        ),
                        time_in_force=TimeInForce.GTC,
                        reduce_only=True,
                        tags=["STOP_LOSS", "FIRST_DELIVERY_RUNNER"],
                    )
                    self.split_stop_order_id = stop.client_order_id
                    self.submit_order(stop)
                    self.lifecycle.append({
                        "type": "FIRST_DELIVERY_STOP_SUBMITTED",
                        "ts_event": self.last_ts_ns,
                        "scenario_id": self.active_plan.scenario_id,
                        "symbol": self.active_symbol,
                        "client_order_id": str(stop.client_order_id),
                        "quantity": str(quantity),
                        "trigger_price": self.active_plan.stop_price,
                    })
                elif not existing.is_closed:
                    current = _decimal(existing.quantity)
                    if current != quantity:
                        self.modify_order(
                            existing,
                            quantity=instrument.make_qty(quantity),
                        )
                        self.lifecycle.append({
                            "type": "FIRST_DELIVERY_STOP_RESIZE_REQUESTED",
                            "ts_event": self.last_ts_ns,
                            "scenario_id": self.active_plan.scenario_id,
                            "symbol": self.active_symbol,
                            "client_order_id": str(existing.client_order_id),
                            "previous_quantity": str(current),
                            "requested_quantity": str(quantity),
                        })
            except Exception as exc:
                self._first_delivery_fail_close(
                    "STOP_SUBMIT_OR_RESIZE_EXCEPTION",
                    {
                        "exception": type(exc).__name__,
                        "message": str(exc),
                        "quantity": str(quantity),
                    },
                )

        def _submit_first_delivery_targets(self) -> None:
            if (
                self.split_targets_submitted
                or self.active_plan is None
                or self.active_symbol is None
                or self.split_fail_closed
            ):
                return
            tolerance = Decimal(str(instruments[self.active_symbol].size_increment)) / 2
            if abs(self.split_entry_filled_qty - self.split_total_qty) > tolerance:
                return
            instrument = instruments[self.active_symbol]
            exit_side = (
                OrderSide.SELL
                if self.active_plan.direction == Direction.LONG
                else OrderSide.BUY
            )
            try:
                primary = self.order_factory.limit(
                    instrument_id=instrument.id,
                    order_side=exit_side,
                    quantity=instrument.make_qty(self.split_primary_qty),
                    price=instrument.make_price(
                        self.active_plan.details["first_delivery_target"]
                    ),
                    time_in_force=TimeInForce.GTC,
                    post_only=True,
                    reduce_only=True,
                    tags=["FIRST_DELIVERY"],
                )
                runner = self.order_factory.limit(
                    instrument_id=instrument.id,
                    order_side=exit_side,
                    quantity=instrument.make_qty(self.split_runner_qty),
                    price=instrument.make_price(self.active_plan.target_price),
                    time_in_force=TimeInForce.GTC,
                    post_only=True,
                    reduce_only=True,
                    tags=["EXTERNAL_RUNNER"],
                )
                self.split_primary_order_id = primary.client_order_id
                self.split_runner_order_id = runner.client_order_id
                self.split_targets_submitted = True
                self.submit_order(primary)
                self.submit_order(runner)
                self.lifecycle.append({
                    "type": "FIRST_DELIVERY_TARGETS_SUBMITTED",
                    "ts_event": self.last_ts_ns,
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": self.active_symbol,
                    "primary_client_order_id": str(primary.client_order_id),
                    "runner_client_order_id": str(runner.client_order_id),
                    "primary_quantity": str(self.split_primary_qty),
                    "runner_quantity": str(self.split_runner_qty),
                    "first_delivery_target": self.active_plan.details[
                        "first_delivery_target"
                    ],
                    "external_runner_target": self.active_plan.target_price,
                })
            except Exception as exc:
                self._first_delivery_fail_close(
                    "TARGET_SUBMISSION_EXCEPTION",
                    {
                        "exception": type(exc).__name__,
                        "message": str(exc),
                    },
                )

        def _handle_first_delivery_fill(self, event: OrderEvent) -> None:
            if not self.split_enabled:
                return
            client_order_id = event.client_order_id
            last_qty = _decimal(getattr(event, "last_qty", None))
            if client_order_id == self.split_entry_order_id:
                entry_order = self._split_order(self.split_entry_order_id)
                if entry_order is None:
                    self._first_delivery_fail_close("ENTRY_ORDER_MISSING_AFTER_FILL")
                    return
                self.split_entry_filled_qty = _decimal(entry_order.filled_qty)
                self._ensure_first_delivery_stop(self.split_entry_filled_qty)
                if entry_order.is_closed:
                    self._submit_first_delivery_targets()
                return

            if client_order_id == self.split_primary_order_id:
                self.split_primary_filled_qty += last_qty
                self.lifecycle.append({
                    "type": "FIRST_DELIVERY_FILLED",
                    "ts_event": int(event.ts_event),
                    "scenario_id": (
                        None if self.active_plan is None else self.active_plan.scenario_id
                    ),
                    "symbol": self.active_symbol,
                    "client_order_id": str(client_order_id),
                    "last_quantity": str(last_qty),
                    "cumulative_quantity": str(self.split_primary_filled_qty),
                    "remaining_quantity": str(self._remaining_split_qty()),
                })
                self._ensure_first_delivery_stop(self._remaining_split_qty())
                return

            if client_order_id == self.split_runner_order_id:
                tolerance = (
                    Decimal(str(instruments[self.active_symbol].size_increment)) / 2
                    if self.active_symbol is not None
                    else Decimal("0")
                )
                if self.split_primary_filled_qty + tolerance < self.split_primary_qty:
                    self._first_delivery_fail_close(
                        "RUNNER_FILLED_BEFORE_PRIMARY_COMPLETION",
                        {
                            "primary_filled": str(self.split_primary_filled_qty),
                            "primary_quantity": str(self.split_primary_qty),
                        },
                    )
                    return
                self.split_runner_filled_qty += last_qty
                self.lifecycle.append({
                    "type": "EXTERNAL_RUNNER_FILLED",
                    "ts_event": int(event.ts_event),
                    "scenario_id": (
                        None if self.active_plan is None else self.active_plan.scenario_id
                    ),
                    "symbol": self.active_symbol,
                    "client_order_id": str(client_order_id),
                    "last_quantity": str(last_qty),
                    "cumulative_quantity": str(self.split_runner_filled_qty),
                    "remaining_quantity": str(self._remaining_split_qty()),
                })
                remaining = self._remaining_split_qty()
                if remaining <= 0:
                    self._cancel_split_order(self.split_stop_order_id)
                else:
                    self._ensure_first_delivery_stop(remaining)
                return

            if client_order_id == self.split_stop_order_id:
                self.split_stop_filled_qty += last_qty
                self.lifecycle.append({
                    "type": "FIRST_DELIVERY_STOP_FILLED",
                    "ts_event": int(event.ts_event),
                    "scenario_id": (
                        None if self.active_plan is None else self.active_plan.scenario_id
                    ),
                    "symbol": self.active_symbol,
                    "client_order_id": str(client_order_id),
                    "last_quantity": str(last_qty),
                    "cumulative_quantity": str(self.split_stop_filled_qty),
                    "remaining_quantity": str(self._remaining_split_qty()),
                })
                self._cancel_split_order(self.split_primary_order_id)
                self._cancel_split_order(self.split_runner_order_id)

        def _release_if_terminal(self, ts_ns: int, reason: str) -> None:''',
        label="first-delivery-methods",
    )

    source = _replace(
        source,
        '''                    self.active_plan = None
                    self.active_symbol = None
                    self.active_position_opened_ns = None
                    self.time_exit_requested = False''',
        '''                    self.active_plan = None
                    self.active_symbol = None
                    self.active_position_opened_ns = None
                    self.time_exit_requested = False
                    self._reset_first_delivery_state()''',
        label="terminal-first-delivery-reset",
        expected=2,
    )

    source = _replace(
        source,
        '''            side = OrderSide.BUY if plan.direction == Direction.LONG else OrderSide.SELL
            try:''',
        '''            side = OrderSide.BUY if plan.direction == Direction.LONG else OrderSide.SELL
            from decimal import ROUND_CEILING
            self.split_enabled = (
                plan.details.get("realization_policy")
                == "SELF_FINANCING_FIRST_DELIVERY_EXTERNAL_RUNNER"
                and plan.details.get("first_delivery_available") is True
            )
            if self.split_enabled:
                increment = Decimal(str(instrument.size_increment))
                fraction = Decimal(
                    str(plan.details["first_delivery_primary_fraction"])
                )
                raw_primary = decision.quantity * fraction
                primary_steps = (raw_primary / increment).to_integral_value(
                    rounding=ROUND_CEILING
                )
                primary_quantity = primary_steps * increment
                runner_quantity = decision.quantity - primary_quantity
                min_quantity = Decimal(str(instrument.min_quantity))
                min_notional = _decimal(instrument.min_notional)
                rounded_margin = (
                    primary_quantity
                    * Decimal(str(plan.details["first_delivery_net_gain_per_unit"]))
                    - runner_quantity
                    * Decimal(str(plan.details["original_costed_loss_per_unit"]))
                )
                split_feasible = (
                    primary_quantity >= min_quantity
                    and runner_quantity >= min_quantity
                    and primary_quantity * Decimal(str(plan.expected_entry)) >= min_notional
                    and runner_quantity * Decimal(str(plan.expected_entry)) >= min_notional
                    and rounded_margin >= 0
                )
                if split_feasible:
                    plan.details["first_delivery_primary_quantity"] = str(
                        primary_quantity
                    )
                    plan.details["external_runner_quantity"] = str(
                        runner_quantity
                    )
                    plan.details["rounded_primary_fraction"] = str(
                        primary_quantity / decision.quantity
                    )
                    plan.details["rounded_runner_fraction"] = str(
                        runner_quantity / decision.quantity
                    )
                    plan.details["rounded_self_financing_margin"] = str(
                        rounded_margin
                    )
                    self.split_total_qty = decision.quantity
                    self.split_primary_qty = primary_quantity
                    self.split_runner_qty = runner_quantity
                else:
                    self.split_enabled = False
                    plan.details["first_delivery_activation"] = (
                        "BASELINE_FALLBACK_EXCHANGE_INFEASIBLE"
                    )
                    plan.details["first_delivery_split_diagnostic"] = {
                        "decision_quantity": str(decision.quantity),
                        "primary_quantity": str(primary_quantity),
                        "runner_quantity": str(runner_quantity),
                        "minimum_quantity": str(min_quantity),
                        "minimum_notional": str(min_notional),
                        "rounded_self_financing_margin": str(rounded_margin),
                    }
            else:
                plan.details["first_delivery_activation"] = (
                    "BASELINE_FALLBACK_NO_CAUSAL_FIRST_DELIVERY"
                )
            if self.split_enabled:
                plan.details["first_delivery_activation"] = "SPLIT_ACTIVE"
                self.lifecycle.append({
                    "type": "FIRST_DELIVERY_SPLIT_ACTIVATED",
                    "ts_event": self.last_ts_ns,
                    "scenario_id": plan.scenario_id,
                    "symbol": symbol,
                    "primary_quantity": str(self.split_primary_qty),
                    "runner_quantity": str(self.split_runner_qty),
                    "first_delivery_target": plan.details["first_delivery_target"],
                    "external_runner_target": plan.target_price,
                })
            else:
                self.lifecycle.append({
                    "type": "FIRST_DELIVERY_BASELINE_FALLBACK",
                    "ts_event": self.last_ts_ns,
                    "scenario_id": plan.scenario_id,
                    "symbol": symbol,
                    "reason": plan.details.get("first_delivery_activation"),
                })
            try:''',
        label="split-quantity-allocation",
    )

    old_parent = NEW_ORDER_BLOCK + '''
                self.submit_order_list(order_list)'''
    new_parent = '''                if self.split_enabled:
                    # first-delivery-single-parent: one pending entry only.
                    if plan.entry_order_type == "MARKET":
                        entry_order = self.order_factory.market(
                            instrument_id=instrument.id,
                            order_side=side,
                            quantity=instrument.make_qty(decision.quantity),
                            time_in_force=TimeInForce.GTC,
                            reduce_only=False,
                            tags=["ENTRY", "FIRST_DELIVERY_PARENT"],
                        )
                    else:
                        entry_order = self.order_factory.limit(
                            instrument_id=instrument.id,
                            order_side=side,
                            quantity=instrument.make_qty(decision.quantity),
                            price=instrument.make_price(plan.expected_entry),
                            expire_time=datetime.fromtimestamp(
                                plan.expire_ts_ns / 1_000_000_000,
                                tz=UTC,
                            ) + timedelta(microseconds=1),
                            time_in_force=TimeInForce.GTD,
                            post_only=bool(plan.entry_post_only),
                            reduce_only=False,
                            tags=["ENTRY", "FIRST_DELIVERY_PARENT"],
                        )
                    self.split_entry_order_id = entry_order.client_order_id
                    self.submit_order(entry_order)
                else:
                    # Preserve the inherited bracket when the split cannot be
                    # expressed causally or at the venue quantity granularity.
                    if plan.entry_order_type == "MARKET":
                        order_list = self.order_factory.bracket(
                            instrument_id=instrument.id,
                            order_side=side,
                            quantity=instrument.make_qty(decision.quantity),
                            entry_order_type=OrderType.MARKET,
                            time_in_force=TimeInForce.GTC,
                            tp_order_type=OrderType.LIMIT,
                            tp_price=instrument.make_price(plan.target_price),
                            tp_time_in_force=TimeInForce.GTC,
                            tp_post_only=True,
                            sl_order_type=OrderType.STOP_MARKET,
                            sl_trigger_price=instrument.make_price(plan.stop_price),
                            sl_time_in_force=TimeInForce.GTC,
                        )
                    else:
                        order_list = self.order_factory.bracket(
                            instrument_id=instrument.id,
                            order_side=side,
                            quantity=instrument.make_qty(decision.quantity),
                            entry_order_type=OrderType.LIMIT,
                            entry_price=instrument.make_price(plan.expected_entry),
                            expire_time=datetime.fromtimestamp(
                                plan.expire_ts_ns / 1_000_000_000,
                                tz=UTC,
                            ) + timedelta(microseconds=1),
                            time_in_force=TimeInForce.GTD,
                            entry_post_only=bool(plan.entry_post_only),
                            tp_order_type=OrderType.LIMIT,
                            tp_price=instrument.make_price(plan.target_price),
                            tp_time_in_force=TimeInForce.GTC,
                            tp_post_only=True,
                            sl_order_type=OrderType.STOP_MARKET,
                            sl_trigger_price=instrument.make_price(plan.stop_price),
                            sl_time_in_force=TimeInForce.GTC,
                        )
                    self.submit_order_list(order_list)'''
    source = _replace(
        source,
        old_parent,
        new_parent,
        label="single-parent-order",
    )

    source = _replace(
        source,
        '''                self._capture_events(symbol)
                return

            self.logic[symbol].mark_submitted(''',
        '''                self._capture_events(symbol)
                self._reset_first_delivery_state()
                return

            self.logic[symbol].mark_submitted(''',
        label="submission-exception-reset",
    )

    source = _replace(
        source,
        '''        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_FILLED")
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")''',
        '''        def on_order_updated(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_UPDATED")
            if event.client_order_id == self.split_stop_order_id:
                self.lifecycle.append({
                    "type": "FIRST_DELIVERY_STOP_RESIZE_CONFIRMED",
                    "ts_event": int(event.ts_event),
                    "scenario_id": (
                        None if self.active_plan is None else self.active_plan.scenario_id
                    ),
                    "symbol": self.active_symbol,
                    "client_order_id": str(event.client_order_id),
                })

        def on_order_modify_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_MODIFY_REJECTED")
            if self.split_enabled:
                self._first_delivery_fail_close(
                    "ORDER_MODIFY_REJECTED",
                    {"event": str(event)},
                )
            else:
                self.errors.append({
                    "type": "ORDER_MODIFY_REJECTED",
                    "event": str(event),
                })

        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_FILLED")
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")
            self._handle_first_delivery_fill(event)
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED_SYNC")''',
        label="first-delivery-fill-events",
    )

    source = _replace(
        source,
        '''        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")

        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")''',
        '''        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            if (
                self.split_enabled
                and self.active_symbol is not None
                and not self.portfolio.is_flat(instruments[self.active_symbol].id)
            ):
                self._first_delivery_fail_close(
                    "ORDER_DENIED_WHILE_NONFLAT",
                    {"event": str(event)},
                )
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")

        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            if (
                self.split_enabled
                and self.active_symbol is not None
                and not self.portfolio.is_flat(instruments[self.active_symbol].id)
            ):
                self._first_delivery_fail_close(
                    "ORDER_REJECTED_WHILE_NONFLAT",
                    {"event": str(event)},
                )
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")''',
        label="first-delivery-rejection-fail-close",
    )

    source = _replace(
        source,
        '''        "resolution_tail_unresolved_count": sum(
            item.get("type") == "RESOLUTION_TAIL_UNRESOLVED"
            for item in errors
        ),
        "success_claim": False,''',
        '''        "resolution_tail_unresolved_count": sum(
            item.get("type") == "RESOLUTION_TAIL_UNRESOLVED"
            for item in errors
        ),
        "first_delivery_split_activated_count": sum(
            item.get("type") == "FIRST_DELIVERY_SPLIT_ACTIVATED"
            for item in lifecycle
        ),
        "first_delivery_baseline_fallback_count": sum(
            item.get("type") == "FIRST_DELIVERY_BASELINE_FALLBACK"
            for item in lifecycle
        ),
        "first_delivery_targets_submitted_count": sum(
            item.get("type") == "FIRST_DELIVERY_TARGETS_SUBMITTED"
            for item in lifecycle
        ),
        "first_delivery_fill_count": sum(
            item.get("type") == "FIRST_DELIVERY_FILLED"
            for item in lifecycle
        ),
        "external_runner_fill_count": sum(
            item.get("type") == "EXTERNAL_RUNNER_FILLED"
            for item in lifecycle
        ),
        "first_delivery_stop_fill_count": sum(
            item.get("type") == "FIRST_DELIVERY_STOP_FILLED"
            for item in lifecycle
        ),
        "first_delivery_stop_resize_request_count": sum(
            item.get("type") == "FIRST_DELIVERY_STOP_RESIZE_REQUESTED"
            for item in lifecycle
        ),
        "first_delivery_fail_closed_count": sum(
            item.get("type") == "FIRST_DELIVERY_FAIL_CLOSED"
            for item in lifecycle
        ),
        "success_claim": False,''',
        label="first-delivery-metrics",
    )

    required = {
        "first-delivery-single-parent": 1,
        "FIRST_DELIVERY_SPLIT_ACTIVATED": 2,
        "FIRST_DELIVERY_BASELINE_FALLBACK": 2,
        "FIRST_DELIVERY_TARGETS_SUBMITTED": 2,
        "FIRST_DELIVERY_FILLED": 2,
        "EXTERNAL_RUNNER_FILLED": 2,
        "FIRST_DELIVERY_STOP_FILLED": 2,
        "FIRST_DELIVERY_FAIL_CLOSED": 2,
        "first_delivery_primary_quantity": 1,
        "SELF_FINANCING_FIRST_DELIVERY_EXTERNAL_RUNNER": 1,
    }
    bad = {
        token: (source.count(token), expected)
        for token, expected in required.items()
        if source.count(token) != expected
    }
    if bad:
        raise RuntimeError(f"first-delivery routes were not materialized: {bad}")
    if source.count("self.submit_order_list(order_list)") != 1:
        raise RuntimeError("baseline fallback bracket was not retained exactly once")
    return source
