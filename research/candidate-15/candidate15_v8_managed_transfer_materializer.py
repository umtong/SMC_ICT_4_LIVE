"""Fail-closed Candidate 15 V8 managed-transfer portfolio materialization."""
from __future__ import annotations


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
            f"Candidate 15 V8 portfolio boundary drifted at {label}: "
            f"expected {expected}, found {count}",
        )
    return source.replace(old, new)


def materialize_managed_transfer_source(source: str) -> str:
    source = _replace(
        source,
        "from decimal import Decimal\n",
        "from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR\n",
        label="decimal-rounding-imports",
    )
    source = _replace(
        source,
        "candidate-15-v7-strict-open-time",
        "candidate-15-v8-strict-open-time",
        label="strict-open-time-identity",
    )
    source = _replace(
        source,
        "BoundedTransferPersistentQuarterHourRouter(",
        "ManagedTransferPersistentQuarterHourRouter(",
        label="managed-transfer-router",
    )
    source = _replace(
        source,
        "BoundedResidualTransferContinuationEngine(",
        "ManagedResidualTransferContinuationEngine(",
        label="managed-transfer-continuation",
    )
    source = _replace(
        source,
        "            self.initiative_key = V7_ROUTER_KEY\n",
        "            self.initiative_key = V8_ROUTER_KEY\n",
        label="managed-transfer-router-key",
    )
    source = _replace(
        source,
        "C15_V7_CORE_FAMILY_QUARANTINED",
        "C15_V8_CORE_FAMILY_QUARANTINED",
        label="core-family-identity",
        expected=3,
    )
    source = _replace(
        source,
        "C15_V7_NOT_RESIDUAL_RECEIVER",
        "C15_V8_NOT_RESIDUAL_RECEIVER",
        label="receiver-ownership-identity",
        expected=3,
    )
    source = _replace(
        source,
        'continuation.details["candidate15_v7_ownership"]',
        'continuation.details["candidate15_v8_ownership"]',
        label="ownership-evidence-identity",
    )
    source = _replace(
        source,
        "            self.last_ts_ns = 0\n",
        "            self.last_ts_ns = 0\n"
        "            self.transfer_protected = False\n",
        label="transfer-protection-state",
    )
    source = _replace(
        source,
        "            self.active_plan = plan\n"
        "            self.active_symbol = symbol\n",
        "            self.active_plan = plan\n"
        "            self.active_symbol = symbol\n"
        "            self.transfer_protected = False\n",
        label="reset-transfer-protection-on-submit",
    )

    marker = "        def _process_batch(self, ts_ns: int) -> None:\n"
    method = '''        def _protect_completed_transfer(self, ts_ns: int) -> None:
            if (
                self.transfer_protected
                or self.active_plan is None
                or self.active_symbol is None
                or self.mutex.state != SlotState.POSITION_OPEN
            ):
                return
            transfer = self.active_plan.details.get("candidate15_v8_transfer")
            if not isinstance(transfer, dict):
                return
            observation = self.buffer.get(self.active_symbol)
            if observation is None:
                return

            instrument = instruments[self.active_symbol]
            instrument_id = instrument.id
            if self.portfolio.is_flat(instrument_id):
                return
            tick = Decimal(str(instrument.price_increment))
            entry = Decimal(str(self.active_plan.expected_entry))
            maker = Decimal(str(execution_config["effective_maker_rate"]))
            taker = Decimal(str(execution_config["effective_taker_rate"]))
            parity = Decimal(str(transfer["parity_price"]))
            close = Decimal(str(observation.close))

            if self.active_plan.direction == Direction.LONG:
                break_even = entry * (Decimal("1") + maker) / (Decimal("1") - taker)
                units = (break_even / tick).to_integral_value(rounding=ROUND_CEILING) + 1
                lock = units * tick
                activation = max(parity, lock + tick)
                crossed = close >= activation
                lock_valid = close > lock
            else:
                break_even = entry * (Decimal("1") - maker) / (Decimal("1") + taker)
                units = (break_even / tick).to_integral_value(rounding=ROUND_FLOOR) - 1
                lock = units * tick
                activation = min(parity, lock - tick)
                crossed = close <= activation
                lock_valid = close < lock
            if not crossed:
                return

            record = {
                "type": "TRANSFER_COMPLETION_CONFIRMED",
                "ts_event": ts_ns,
                "scenario_id": self.active_plan.scenario_id,
                "symbol": self.active_symbol,
                "stage": transfer.get("stage"),
                "completed_close": str(close),
                "parity_price": str(parity),
                "activation_price": str(activation),
                "cost_cover_stop": str(lock),
            }
            self.lifecycle.append(record)
            if not lock_valid:
                self.errors.append({
                    **record,
                    "type": "TRANSFER_COST_COVER_ALREADY_IN_MARKET",
                })
                if self.cache.orders_open_count(
                    instrument_id=instrument_id,
                    strategy_id=self.id,
                ):
                    self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
                self.transfer_protected = True
                return

            stop_orders = [
                order
                for order in self.cache.orders_open(
                    instrument_id=instrument_id,
                    strategy_id=self.id,
                )
                if order.order_type == OrderType.STOP_MARKET
            ]
            if len(stop_orders) != 1:
                self.errors.append({
                    **record,
                    "type": "TRANSFER_PROTECTIVE_STOP_NOT_UNIQUE",
                    "open_stop_orders": len(stop_orders),
                })
                if self.cache.orders_open_count(
                    instrument_id=instrument_id,
                    strategy_id=self.id,
                ):
                    self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
                self.transfer_protected = True
                return

            stop_order = stop_orders[0]
            current_trigger = Decimal(str(stop_order.trigger_price))
            already_protected = (
                current_trigger >= lock
                if self.active_plan.direction == Direction.LONG
                else current_trigger <= lock
            )
            if already_protected:
                self.lifecycle.append({
                    **record,
                    "type": "TRANSFER_STOP_ALREADY_PROTECTED",
                    "client_order_id": str(stop_order.client_order_id),
                    "current_trigger": str(current_trigger),
                })
                self.transfer_protected = True
                return
            try:
                self.modify_order(
                    stop_order,
                    trigger_price=instrument.make_price(lock),
                )
            except Exception as exc:
                self.errors.append({
                    **record,
                    "type": "TRANSFER_STOP_MODIFICATION_EXCEPTION",
                    "client_order_id": str(stop_order.client_order_id),
                    "exception": type(exc).__name__,
                    "message": str(exc),
                })
                if self.cache.orders_open_count(
                    instrument_id=instrument_id,
                    strategy_id=self.id,
                ):
                    self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
                self.transfer_protected = True
                return
            self.lifecycle.append({
                **record,
                "type": "TRANSFER_STOP_MODIFICATION_SUBMITTED",
                "client_order_id": str(stop_order.client_order_id),
                "previous_trigger": str(current_trigger),
                "new_trigger": str(lock),
            })
            self.transfer_protected = True

'''
    source = _replace(
        source,
        marker,
        method + marker,
        label="completed-transfer-protection-method",
    )
    source = _replace(
        source,
        '''            initiative_state = self.logic[self.initiative_key].on_batch(
                ts_ns,
                self.buffer,
            )''',
        '''            self._protect_completed_transfer(ts_ns)
            initiative_state = self.logic[self.initiative_key].on_batch(
                ts_ns,
                self.buffer,
            )''',
        label="completed-transfer-protection-call",
    )

    old_denied = '''        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")
'''
    new_denied = '''        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            ts_ns = int(event.ts_event)
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            if self.active_plan is not None and self.active_symbol is not None:
                instrument_id = instruments[self.active_symbol].id
                if not self.portfolio.is_flat(instrument_id):
                    self.lifecycle.append({
                        "type": "PROTECTIVE_ORDER_DENIED_FAIL_CLOSED",
                        "ts_event": ts_ns,
                        "scenario_id": self.active_plan.scenario_id,
                        "symbol": self.active_symbol,
                        "denied_client_order_id": str(event.client_order_id),
                    })
                    if self.cache.orders_open_count(
                        instrument_id=instrument_id,
                        strategy_id=self.id,
                    ):
                        self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
            self._release_if_terminal(ts_ns, "ORDER_DENIED")
'''
    source = _replace(
        source,
        old_denied,
        new_denied,
        label="protective-denial-fail-close",
    )

    required = {
        "candidate-15-v8-strict-open-time": 1,
        "ManagedTransferPersistentQuarterHourRouter(": 1,
        "ManagedResidualTransferContinuationEngine(": 1,
        "            self.initiative_key = V8_ROUTER_KEY\n": 1,
        "C15_V8_CORE_FAMILY_QUARANTINED": 3,
        "C15_V8_NOT_RESIDUAL_RECEIVER": 3,
        'continuation.details["candidate15_v8_ownership"]': 1,
        "TRANSFER_STOP_MODIFICATION_SUBMITTED": 1,
        "TRANSFER_PROTECTIVE_STOP_NOT_UNIQUE": 1,
        "PROTECTIVE_ORDER_DENIED_FAIL_CLOSED": 1,
        "self._protect_completed_transfer(ts_ns)": 1,
    }
    bad = {
        token: (source.count(token), expected)
        for token, expected in required.items()
        if source.count(token) != expected
    }
    if bad:
        raise RuntimeError(f"Candidate 15 V8 routes were not materialized: {bad}")
    for stale in (
        "candidate-15-v7-strict-open-time",
        "C15_V7_CORE_FAMILY_QUARANTINED",
        "C15_V7_NOT_RESIDUAL_RECEIVER",
        'continuation.details["candidate15_v7_ownership"]',
    ):
        if stale in source:
            raise RuntimeError(f"stale V7 identity survived V8 materialization: {stale}")
    return source
