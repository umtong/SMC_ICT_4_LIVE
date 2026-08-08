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
                self.lifecycle.append({\n                    "type": "FIRST_DELIVERY_TARGETS_SUBMITTED",
                    "ts_event": self.last_ts_ns,
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": self.active_symbol,
                    "primary_client_order_id": str(primary.client_order_id),
                    "runner_client_order_id": str(runner.client_order_id),
                    "primary_quantity": str(self.split_primary_qty),
                    "runner_quantity": str(self.split_runner_qty),
                    "first_delivery_target": self.active_plan.details[\n                        "first_delivery_target"\n                    ],
                    "external_runner_target": self.active_plan.target_price,
                })
            except Exception as exc:
                self._first_delivery_fail_close(\n                    "TARGET_SUBMISSION_EXCEPTION",
                    {\n                        "exception": type(exc).__name__,\n                        "message": str(exc),\n                    },\n                )

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
                self.lifecycle.append({\n                    "type": "FIRST_DELIVERY_FILLED",\n                    "ts_event": int(event.ts_event),\n                    "scenario_id": (\n                        None if self.active_plan is None else self.active_plan.scenario_id\n                    ),\n                    "symbol": self.active_symbol,\n                    "client_order_id": str(client_order_id),\n                    "last_quantity": str(last_qty),\n                    "cumulative_quantity": str(self.split_primary_filled_qty),\n                    "remaining_quantity": str(self._remaining_split_qty()),\n                })
                self._ensure_first_delivery_stop(self._remaining_split_qty())
                return

            if client_order_id == self.split_runner_order_id:
                tolerance = (\n                    Decimal(str(instruments[self.active_symbol].size_increment)) / 2\n                    if self.active_symbol is not None\n                    else Decimal("0")\n                )
                if self.split_primary_filled_qty + tolerance < self.split_primary_qty:
                    self._first_delivery_fail_close(\n                        "RUNNER_FILLED_BEFORE_PRIMARY_COMPLETION",\n                        {\n                            "primary_filled": str(self.split_primary_filled_qty),\n                            "primary_quantity": str(self.split_primary_qty),\n                        },\n                    )
                    return
                self.split_runner_filled_qty += last_qty
                self.lifecycle.append({\n                    "type": "EXTERNAL_RUNNER_FILLED",\n                    "ts_event": int(event¹ÑÍ}•Ù•¹Ð¤±q¸€€€€€€€€€€€€€€€€€€€€‰Í•¹…É¥½}¥ˆè€¡q¸€€€€€€€€€€€€€€€€€€€€€€€9½¹”¥˜Í•±˜¹…Ñ¥Ù•}Á±…¸¥Ì9½¹”•±Í”Í•±˜¹…Ñ¥Ù•}Á±…¸¹Í•¹…É¥½}¥‘q¸€€€€€€€€€€€€€€€€€€€€¤±q¸€€€€€€€€€€€€€€€€€€€€‰Íåµ‰½°ˆèÍ•±˜¹…Ñ¥Ù•}Íåµ‰½°±q¸€€€€€€€€€€€€€€€€€€€€‰±¥•¹Ñ}½É‘•É}¥ˆèÍÑÈ¡±¥•¹Ñ}½É‘•É}¥¤±q¸€€€€€€€€€€€€€€€€€€€€‰±…ÍÑ}ÅÕ…¹Ñ¥ÑäˆèÍÑÈ¡±…ÍÑ}ÅÑä¤±q¸€€€€€€€€€€€€€€€€€€€€‰ÕµÕ±…Ñ¥Ù•}ÅÕ…¹Ñ¥ÑäˆèÍÑÈ¡Í•±˜¹ÍÁ±¥Ñ}ÉÕ¹¹•É}™¥±±•‘}ÅÑä¤±q¸€€€€€€€€€€€€€€€€€€€€‰É•µ…¥¹¥¹}ÅÕ…¹Ñ¥ÑäˆèÍÑÈ¡Í•±˜¹}É•µ…¥¹¥¹}ÍÁ±¥Ñ}ÅÑä ¤¤±q¸€€€€€€€€€€€€€€€ô¤(€€€€€€€€€€€€€€€É•µ…¥¹¥¹œ€ôÍ•±˜¹}É•µ…¥¹¥¹}ÍÁ±¥Ñ}ÅÑä ¤(€€€€€€€€€€€€€€€¥˜É•µ…¥¹¥¹œ€ðô€Àè(€€€€€€€€€€€€€€€€€€€Í•±˜¹}…¹•±}ÍÁ±¥Ñ}½É‘•È¡Í•±˜¹ÍÁ±¥Ñ}ÍÑ½Á}½É‘•É}¥¤(€€€€€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€€€€€Í•±˜¹}•¹ÍÕÉ•}™¥ÉÍÑ}‘•±¥Ù•Éå}ÍÑ½À¡É•µ…¥¹¥¹œ¤(€€€€€€€€€€€€€€€É•ÑÕÉ¸((€€€€€€€€€€€¥˜±¥•¹Ñ}½É‘•É}¥€ôôÍ•±˜¹ÍÁ±¥Ñ}ÍÑ½Á}½É‘•É}¥è(€€€€€€€€€€€€€€€Í•±˜¹ÍÁ±¥Ñ}ÍÑ½Á}™¥±±•‘}ÅÑä€¬ô±…ÍÑ}ÅÑä(€€€€€€€€€€€€€€€Í•±˜¹±¥™•å±”¹…ÁÁ•¹¡íq¸€€€€€€€€€€€€€€€€€€€€‰ÑåÁ”ˆè€‰%IMQ}1%YIe}MQ=A}%11ˆ±q¸€€€€€€€€€€€€€€€€€€€€‰ÑÍ}•Ù•¹Ðˆè¥¹Ð¡•Ù•¹Ð¹ÑÍ}•Ù•¹Ð¤±q¸€€€€€€€€€€€€€€€€€€€€‰Í•¹…É¥½}¥ˆè€¡q¸€€€€€€€€€€€€€€€€€€€€€€€9½¹”¥˜Í•±˜¹…Ñ¥Ù•}Á±…¸¥Ì9½¹”•±Í”Í•±˜¹…Ñ¥Ù•}Á±…¸¹Í•¹…É¥½}¥‘q¸€€€€€€€€€€€€€€€€€€€€¤±q¸€€€€€€€€€€€€€€€€€€€€‰Íåµ‰½°ˆèÍ•±˜¹…Ñ¥Ù•}Íåµ‰½°±q¸€€€€€€€€€€€€€€€€€€€€‰±¥•¹Ñ}½É‘•É}¥ˆèÍÑÈ¡±¥•¹Ñ}½É‘•É}¥¤±q¸€€€€€€€€€€€€€€€€€€€€‰±…ÍÑ}ÅÕ…¹Ñ¥ÑäˆèÍÑÈ¡±…ÍÑ}ÅÑä¤±q¸€€€€€€€€€€€€€€€€€€€€‰ÕµÕ±…Ñ¥Ù•}ÅÕ…¹Ñ¥ÑäˆèÍÑÈ¡Í•±˜¹ÍÁ±¥Ñ}ÍÑ½Á}™¥±±•‘}ÅÑä¤±q¸€€€€€€€€€€€€€€€€€€€€‰É•µ…¥¹¥¹}ÅÕ…¹Ñ¥ÑäˆèÍÑÈ¡Í•±˜¹}É•µ…¥¹¥¹}ÍÁ±¥Ñ}ÅÑä ¤¤±q¸€€€€€€€€€€€€€€€ô¤(€€€€€€€€€€€€€€€Í•±˜¹}…¹•±}ÍÁ±¥Ñ}½É‘•È¡Í•±˜¹ÍÁ±¥Ñ}ÁÉ¥µ…Éå}½É‘•É}¥¤(€€€€€€€€€€€€€€€Í•±˜¹}…¹•±}ÍÁ±¥Ñ}½É‘•È¡Í•±˜¹ÍÁ±¥Ñ}ÉÕ¹¹•É}½É‘•É}¥¤((€€€€€€€‘•˜}É•±•…Í•}¥™}Ñ•Éµ¥¹…°¡Í•±˜°ÑÍ}¹Ì€è¥¹Ð°É•…Í½¸€èÍÑÈ¤€´ø9½¹”èœœœ°(€€€€€€€±…‰•°ô‰™¥ÉÍÐµ‘•±¥Ù•Éäµµ•Ñ¡½‘Ìˆ°(€€€€¤((€€€Í½ÕÉ”€ô}É•Á±…” (€€€€€€€Í½ÕÉ”°(€€€€€€€€œœœ€€€€€€€€€€€€€€€€€€€Í•±˜¹…Ñ¥Ù•}Á±…¸€ô9½¹”(€€€€€€€€€€€€€€€€€€€Í•±˜¹…Ñ¥Ù•}Íåµ‰½°€ô9½¹”(€€€€€€€€€€€€€€€€€€€Í•±˜¹…Ñ¥Ù•}Á½Í¥Ñ¥½¹}½Á•¹•‘}¹Ì€ô9½¹”(€€€€€€€€€€€€€€€€€€€Í•±˜¹Ñ¥µ•}•á¥Ñ}É•ÅÕ•ÍÑ•€ô…±Í”œœœ°(€€€€€€€€œœœ€€€€€€€€€€€€€€€€€€€Í•±˜¹…Ñ¥Ù•}Á±…¸€ô9½¹”(€€€€€€€€€€€€€€€€€€€Í•±˜¹…Ñ¥Ù•}Íåµ‰½°€ô9½¹”(€€€€€€€€€€€€€€€€€€€Í•±˜¹…Ñ¥Ù•}Á½Í¥Ñ¥½¹}½Á•¹•‘}¹Ì€ô9½¹”(€€€€€€€€€€€€€€€€€€€Í•±˜¹Ñ¥µ•}•á¥Ñ}É•ÅÕ•ÍÑ•€ô…±Í”(€€€€€€€€€€€€€€€€€€€Í•±˜¹}É•Í•Ñ}™¥ÉÍÑ}‘•±¥Ù•Éå}ÍÑ…Ñ” ¤œœœ°(€€€€€€€±…‰•°ô‰Ñ•Éµ¥¹…°µ™¥ÉÍÐµ‘•±¥Ù•ÉäµÉ•Í•Ðˆ°(€€€€€€€•áÁ•Ñ•ôÈ°(€€€€¤((€€€½±‘}Á…É•¹Ð€ô9]}=II}	1=,(€€€¹•Ý}Á…É•¹Ð€ô€œœœ€€€€€€€€€€€€€€€€Œ™¥ÉÍÐµ‘•±¥Ù•ÉäµÍ¥¹±”µÁ…É•¹Ðè¹•Üµ•¹ÑÉäµÕÑ•àÉ•µ…¥¹ÌÍ¥¹Õ±…È¸(€€€€€€€€€€€€€€€¥˜Í•±˜¹ÍÁ±¥Ñ}•¹…‰±•è(€€€€€€€€€€€€€€€€€€€¥˜Á±…¸¹•¹ÑÉå}½É‘•É}ÑåÁ”€ôô€‰5I-Pˆè(€€€€€€€€€€€€€€€€€€€€€€€•¹ÑÉå}½É‘•È€ôÍ•±˜¹½É‘•É}™…Ñ½Éä¹µ…É­•Ð (€€€€€€€€€€€€€€€€€€€€€€€€€€€¥¹ÍÑÉÕµ•¹Ñ}¥õ¥¹ÍÑÉÕµ•¹Ð¹¥°(€€€€€€€€€€€€€€€€€€€€€€€€€€€½É‘•É}Í¥‘”õÍ¥‘”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÅÕ…¹Ñ¥Ñäõ¥¹ÍÑÉÕµ•¹Ð¹µ…­•}ÅÑä¡‘•¥Í¥½¸¹ÅÕ…¹Ñ¥Ñä¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Ñ¥µ•}¥¹}™½É”õQ¥µ•%¹½É”¹Q°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Ñ…Ìõl‰9QIdˆ°€‰%IMQ}1%YIe}IU99H‰t°(€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€€€€€€€€€•¹ÑÉå}½É‘•È€ôÍ•±˜¹½É‘•É}™…Ñ½Éä¹±¥µ¥Ð (€€€€€€€€€€€€€€€€€€€€€€€€€€€¥¹ÍÑÉÕµ•¹Ñ}¥õ¥¹ÍÑÉÕµ•¹Ð¹¥°(€€€€€€€€€€€€€€€€€€€€€€€€€€€½É‘•É}Í¥‘”õÍ¥‘”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÅÕ…¹Ñ¥Ñäõ¥¹ÍÑÉÕµ•¹Ð¹µ…­•}ÅÑä¡‘•¥Í¥½¸¹ÅÕ…¹Ñ¥Ñä¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÁÉ¥”õ¥¹ÍÑÉÕµ•¹Ð¹µ…­•}ÁÉ¥”¡Á±…¸¹•áÁ•Ñ•‘}•¹ÑÉä¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€•áÁ¥É•}Ñ¥µ”õ‘…Ñ•Ñ¥µ”¹™É½µÑ¥µ•ÍÑ…µÀ¡Á±…¸¹•áÁ¥É•}ÑÍ}¹Ì€¼€Å|ÀÀÁ|ÀÀÁ|ÀÀÀ°ÑèõUQ¤€¬Ñ¥µ•‘•±Ñ„¡µ¥É½Í•½¹‘ÌôÄ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Ñ¥µ•}¥¹}™½É”õQ¥µ•%¹½É”¹Q(€€€€€€€€€€€€€€€€€€€€€€€€€€€•¹ÑÉå}Á½ÍÑ}½¹±äõ‰½½°¡Á±…¸¹•¹ÑÉå}Á½ÍÑ}½¹±ä¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Ñ…Ìõl‰9QIdˆ°€‰%IMQ}1%YIe}IU99H‰t°(€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€Í•±˜¹ÍÁ±¥Ñ}•¹ÑÉå}½É‘•É}¥€ô•¹ÑÉå}½É‘•È¹±¥•¹Ñ}½É‘•É}¥(€€€€€€€€€€€€€€€€€€€Í•±˜¹ÍÕ‰µ¥Ñ}½É‘•È¡•¹ÑÉå}½É‘•È¤(€€€€€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€€€€€¥˜Á±…¸¹•¹ÑÉå}½É‘•É}ÑåÁ”€ôô€‰5I-Pˆè(€€€€€€€€€€€€€€€€€€€€€€€½É‘•É}±¥ÍÐ€ôÍ•±˜¹½É‘•É}™…Ñ½Éä¹‰É…­•Ð (€€€€€€€€€€€€€€€€€€€€€€€€€€€¥¹ÍÑÉÕµ•¹Ñ}¥õ¥¹ÍÑÉÕµ•¹Ð¹¥°(€€€€€€€€€€€€€€€€€€€€€€€€€€€½É‘•É}Í¥‘”õÍ¥‘”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÅÕ…¹Ñ¥Ñäõ¥¹ÍÑÉÕµ•¹Ð¹µ…­•}ÅÑä¡‘•¥Í¥½¸¹ÅÕ…¹Ñ¥Ñä¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€•¹ÑÉå}½É‘•É}ÑåÁ”õ=É‘•ÉQåÁ”¹5I-P°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Ñ¥µ•}¥¹}™½É”õQ¥µ•%¹½É”¹Q°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÑÁ}½É‘•É}ÑåÁ”õ=É‘•ÉQåÁ”¹1%5%P°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÑÁ}ÁÉ¥”õ¥¹ÍÑÉÕµ•¹Ð¹µ…­•}ÁÉ¥”¡Á±…¸¹Ñ…É•Ñ}ÁÉ¥”¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÑÁ}Ñ¥µ•}¥¹}™½É”õQ¥µ•%¹½É”¹Q°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÑÁ}Á½ÍÑ}½¹±äõQÉÕ”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Í±}½É‘•É}ÑåÁ”õ=É‘•ÉQåÁ”¹MQ=A}5I-P°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Í±}ÑÉ¥•É}ÁÉ¥”õ¥¹ÍÑÉÕµ•¹Ð¹µ…­•}ÁÉ¥”¡Á±…¸¹ÍÑ½Á}ÁÉ¥”¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Í±}Ñ¥µ•}¥¹}™½É”õQ¥µ•%¹½É”¹Q°(€€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€€€€€€€€€½É‘•É}±¥ÍÐ€ôÍ•±˜¹½É‘•É}™…Ñ½Éä¹‰É…­•Ð (€€€€€€€€€€€€€€€€€€€€€€€€€€€¥¹ÍÑÉÕµ•¹Ñ}¥õ¥¹ÍÑÉÕµ•¹Ð¹¥°(€€€€€€€€€€€€€€€€€€€€€€€€€€€½É‘•É}Í¥‘”õÍ¥‘”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÅÕ…¹Ñ¥Ñäõ¥¹ÍÑÉÕµ•¹Ð¹µ…­•}ÅÑä¡‘•¥Í¥½¸¹ÅÕ…¹Ñ¥Ñä¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€•¹ÑÉå}½É‘•É}ÑåÁ”õ=É‘•ÉQåÁ”¹1%5%P°(€€€€€€€€€€€€€€€€€€€€€€€€€€€•¹ÑÉå}ÁÉ¥”õ¥¹ÍÑÉÕµ•¹Ð¹µ…­•}ÁÉ¥”¡Á±…¸¹•áÁ•Ñ•‘}•¹ÑÉä¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€•áÁ¥É•}Ñ¥µ”õ‘…Ñ•Ñ¥µ”¹™É½µÑ¥µ•ÍÑ…µÀ¡Á±…¸¹•áÁ¥É•}ÑÍ}¹Ì€¼€Å|ÀÀÁ|ÀÀÁ|ÀÀÀ°ÑèõUQ¤€¬Ñ¥µ•‘•±Ñ„¡µ¥É½Í•½¹‘ÌôÄ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Ñ¥µ•}¥¹}™½É”õQ¥µ•%¹½É”¹Q°(€€€€€€€€€€€€€€€€€€€€€€€€€€€•¹ÑÉå}Á½ÍÑ}½¹±äõ‰½½°¡Á±…¸¹•¹ÑÉå}Á½ÍÑ}½¹±ä¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÑÁ}½É‘•É}ÑåÁ”õ=É‘•ÉQåÁ”¹1%5%P°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÑÁ}ÁÉ¥”õ¥¹ÍÑÉÕµ•¹Ð¹µ…­•}ÁÉ¥”¡Á±…¸¹Ñ…É•Ñ}ÁÉ¥”¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÑÁ}Ñ¥µ•}¥¹}™½É”õQ¥µ•%¹½É”¹Q°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÑÁ}Á½ÍÑ}½¹±äõQÉÕ”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Í±}½É‘•É}ÑåÁ”õ=É‘•ÉQåÁ”¹MQ=A}5I-P°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Í±}ÑÉ¥•É}ÁÉ¥”õ¥¹ÍÑÉÕµ•¹Ð¹µ…­•}ÁÉ¥”¡Á±…¸¹ÍÑ½Á}ÁÉ¥”¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Í±}Ñ¥µ•}¥¹}™½É”õQ¥µ•%¹½É”¹Q±¹œ€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€Í•±˜¹ÍÕ‰µ¥Ñ}½É‘•É}±¥ÍÐ¡½É‘•É}±¥ÍÐ¤œœœ(€€€Í½ÕÉ”€ô}É•Á±…” (€€€€€€€Í½ÕÉ”°(€€€€€€€½±‘}Á…É•¹Ð°(€€€€€€€¹•Ý}Á…É•¹Ð°(€€€€€€€±…‰•°ô‰Í¥¹±”µÁ…É•¹Ðµ½É‘•Èˆ°(€€€€¤((€€€Í½ÕÉ”€ô}É•Á±…” (€€€€€€€Í½ÕÉ”°(€€€€€€€€œœœ€€€€€€€€€€€€€€€Í•±˜¹}…ÁÑÕÉ•}•Ù•¹ÑÌ¡Íåµ‰½°¤(€€€€€€€€€€€€€€€É•ÑÕÉ¸((€€€€€€€€€€€Í•±˜¹±½¥mÍåµ‰½±t¹µ…É­}ÍÕ‰µ¥ÑÑ• œœœ°(€€€€€€€€œœœ€€€€€€€€€€€€€€€Í•±˜¹}…ÁÑÕÉ•}•Ù•¹ÑÌ¡Íåµ‰½°¤(€€€€€€€€€€€€€€€Í•±˜¹}É•Í•Ñ}™¥ÉÍÑ}‘•±¥Ù•Éå}ÍÑ…Ñ” ¤(€€€€€€€€€€€€€€€É•ÑÕÉ¸((€€€€€€€€€€€Í•±˜¹±½¥mÍåµ‰½±t¹µ…É­}ÍÕ‰µ¥ÑÑ• œœœ°(€€€€€€€±…‰•°ô‰ÍÕ‰µ¥ÍÍ¥½¸µ•á•ÁÑ¥½¸µÉ•Í•Ðˆ°(€€€€¤((€€€Í½ÕÉ”€ô}É•Á±…” (€€€€€€€Í½ÕÉ”°(€€€€€€€€œœœ€€€€€€€‘•˜½¹}½É‘•É}™¥±±•¡Í•±˜°•Ù•¹Ðè=É‘•ÉÙ•¹Ð¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹}É•½É‘}½É‘•É}•Ù•¹Ð¡•Ù•¹Ð°€‰=II}%11ˆ¤(€€€€€€€€€€€Í•±˜¹}É•±•…Í•}¥™}Ñ•Éµ¥¹…°¡¥¹Ð¡•Ù•¹Ð¹ÑÍ}•Ù•¹Ð¤°€‰=II}%11ˆ¤œœœ°(€€€€€€€€œœœ€€€€€€€‘•˜½¹}½É‘•É}ÕÁ‘…Ñ•¡Í•±˜°•Ù•¹Ðè=É‘•ÉÙ•¹Ð¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹}É•½É‘}½É‘•É}•Ù•¹Ð¡•Ù•¹Ð°€‰=II}UAQˆ¤(€€€€€€€€€€€¥˜•Ù•¹Ð¹±¥•¹Ñ}½É‘•É}¥€ôôÍ•±˜¹ÍÁ±¥Ñ}ÍÑ½Á}½É‘•É}¥è(€€€€€€€€€€€€€€€Í•±˜¹±¥™•å±”¹…ÁÁ•¹¡ì(€€€€€€€€€€€€€€€€€€€€‰ÑåÁ”ˆè€‰%IMQ}1%YIe}MQ=A}IM%i}=9%I5ˆ°(€€€€€€€€€€€€€€€€€€€€‰ÑÍ}•Ù•¹Ðˆè¥¹Ð¡•Ù•¹Ð¹ÑÍ}•Ù•¹Ð¤°(€€€€€€€€€€€€€€€€€€€€‰Í•¹…É¥½}¥ˆè€ (€€€€€€€€€€€€€€€€€€€€€€€9½¹”¥˜Í•±˜¹…Ñ¥Ù•}Á±…¸¥Ì9½¹”•±Í”Í•±˜¹…Ñ¥Ù•}Á±…¸¹Í•¹…É¥½}¥(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€‰Íåµ‰½°ˆèÍ•±˜¹…Ñ¥Ù•}Íåµ‰½°°(€€€€€€€€€€€€€€€€€€€€‰±¥•¹Ñ}½É‘•É}¥ˆèÍÑÈ¡•Ù•¹Ð¹±¥•¹Ñ}½É‘•É}¥¤°(€€€€€€€€€€€€€€€ô¤((€€€€€€€‘•˜½¹}½É‘•É}µ½‘¥™å}É•©•Ñ•¡Í•±˜°•Ù•¹Ðè=É‘•ÉÙ•¹Ð¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹}É•½É‘}½É‘•É}•Ù•¹Ð¡•Ù•¹Ð°€‰=II}5=%e}I)Qˆ¤(€€€€€€€€€€€¥˜Í•±˜¹ÍÁ±¥Ñ}•¹…‰±•è(€€€€€€€€€€€€€€€Í•±˜¹}™¥ÉÍÑ}‘•±¥Ù•Éå}™…¥±}±½Í” (€€€€€€€€€€€€€€€€€€€€‰=II}5=%e}I)Qˆ°(€€€€€€€€€€€€€€€€€€€ì‰•Ù•¹ÐˆèÍÑÈ¡•Ù•¹Ð¥ô°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€Í•±˜¹•ÉÉ½ÉÌ¹…ÁÁ•¹¡ì(€€€€€€€€€€€€€€€€€€€€‰ÑåÁ”ˆè€‰=II}5=%e}I)Qˆ°(€€€€€€€€€€€€€€€€€€€€‰•Ù•¹ÐˆèÍÑÈ¡•Ù•¹Ð¤°(€€€€€€€€€€€€€€€ô¤((€€€€€€€‘•˜½¹}½É‘•É}™¥±±•¡Í•±˜°•Ù•¹Ðè=É‘•ÉÙ•¹Ð¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹}É•½É‘}½É‘•É}•Ù•¹Ð¡•Ù•¹Ð°€‰=II}%11ˆ¤(€€€€€€€€€€€Í•±˜¹}É•±•…Í•}¥™}Ñ•Éµ¥¹…°¡¥¹Ð¡•Ù•¹Ð¹ÑÍ}•Ù•¹Ð¤°€‰=II}%11ˆ¤(€€€€€€€€€€€Í•±˜¹}¡…¹‘±•}™¥ÉÍÑ}‘•±¥Ù•Éå}™¥±°¡•Ù•¹Ð¤(€€€€€€€€€€€Í•±˜¹}É•±•…Í•}¥™}Ñ•Éµ¥¹…°¡¥¹Ð¡•Ù•¹Ð¹ÑÍ}•Ù•¹Ð¤°€‰=II}%11}Me9ˆ¤œœœ°(€€€€€€€±…‰•°ô‰™¥ÉÍÐµ‘•±¥Ù•Éäµ™¥±°µ•Ù•¹ÑÌˆ°(€€€€¤((€€€Í½ÕÉ”€ô}É•Á±…” (€€€€€€€Í½ÕÉ”°(€€€€€€€€œœœ€€€€€€€‘•˜½¹}½É‘•É}‘•¹¥•¡Í•±˜°•Ù•¹Ðè=É‘•ÉÙ•¹Ð¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹}É•½É‘}½É‘•É}•Ù•¹Ð¡•Ù•¹Ð°€‰=II}9%ˆ¤(€€€€€€€€€€€Í•±˜¹•ÉÉ½ÉÌ¹…ÁÁ•¹¡ì‰ÑåÁ”ˆè€‰=II}9%ˆ°€‰•Ù•¹ÐˆèÍÑÈ¡•Ù•¹Ð¥ô¤(€€€€€€€€€€€Í•±˜¹}É•±•…Í•}¥™}Ñ•Éµ¥¹…°¡¥¹Ð¡•Ù•¹Ð¹ÑÍ}•Ù•¹Ð¤°€‰=II}9%ˆ¤((€€€€€€€‘•˜½¹}½É‘•É}É•©•Ñ•¡Í•±˜°•Ù•¹Ðè=É‘•ÉÙ•¹Ð¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹}É•½É‘}½É‘•É}•Ù•¹Ð¡•Ù•¹Ð°€‰=II}I)Qˆ¤(€€€€€€€€€€€Í•±˜¹•ÉÉ½ÉÌ¹…ÁÁ•¹¡ì‰ÑåÁ”ˆè€‰=II}I)Qˆ°€‰•Ù•¹ÐˆèÍÑÈ¡•Ù•¹Ð¥ô¤(€€€€€€€€€€€Í•±˜¹}É•±•…Í•}¥™}Ñ•Éµ¥¹…°¡¥¹Ð¡•Ù•¹Ð¹ÑÍ}•Ù•¹Ð¤°€‰=II}I)Qˆ¤œœœ°(€€€€€€€€œœœ€€€€€€€‘•˜½¹}½É‘•É}‘•¹¥•¡Í•±˜°•Ù•¹Ðè=É‘•ÉÙ•¹Ð¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹}É•½É‘}½É‘•É}•Ù•¹Ð¡•Ù•¹Ð°€‰=II}9%ˆ¤(€€€€€€€€€€€Í•±˜¹•ÉÉ½ÉÌ¹…ÁÁ•¹¡ì‰ÑåÁ”ˆè€‰=II}9%ˆ°€‰•Ù•¹ÐˆèÍÑÈ¡•Ù•¹Ð¥ô¤(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€Í•±˜¹ÍÁ±¥Ñ}•¹…‰±•(€€€€€€€€€€€€€€€…¹Í•±˜¹…Ñ¥Ù•}Íåµ‰½°¥Ì¹½Ð9½¹”(€€€€€€€€€€€€€€€…¹¹½ÐÍ•±˜¹Á½ÉÑ™½±¥¼¹¥Í}™±…Ð¡¥¹ÍÑÉÕµ•¹ÑÍmÍ•±˜¹…Ñ¥Ù•}Íåµ‰½±t¹¥¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€Í•±˜¹}™¥ÉÍÑ}‘•±¥Ù•Éå}™…¥±}±½Í” (€€€€€€€€€€€€€€€€€€€€‰=II}9%]}]!%1}9=91Pˆ°(€€€€€€€€€€€€€€€€€€€ì‰•Ù•¹ÐˆèÍÑÈ¡•Ù•¹Ð¥ô°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€Í•±˜¹}É•±•…Í•}¥™}Ñ•Éµ¥¹…°¡¥¹Ð¡•Ù•¹Ð¹ÑÍ}•Ù•¹Ð¤°€‰=II}9%ˆ¤((€€€€€€€‘•˜½¹}½É‘•É}É•©•Ñ•¡Í•±˜°•Ù•¹Ðè=É‘•ÉÙ•¹Ð¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹}É•½É‘}½É‘•É}•Ù•¹Ð¡•Ù•¹Ð°€‰=II}I)Qˆ¤(€€€€€€€€€€€Í•±˜¹•ÉÉ½ÉÌ¹…ÁÁ•¹¡ì‰ÑåÁ”ˆè€‰=II}I)Qˆ°€‰•Ù•¹ÐˆèÍÑÈ¡•Ù•¹Ð¥ô¤(€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€Í•±˜¹ÍÁ±¥Ñ}•¹…‰±•(€€€€€€€€€€€€€€€…¹Í•±˜¹…Ñ¥Ù•}Íåµ‰½°¥Ì¹½Ð9½¹”(€€€€€€€€€€€€€€€…¹¹½ÐÍ•±˜¹Á½ÉÑ™½±¥¼¹¥Í}™±…Ð¡¥¹ÍÑÉÕµ•¹ÑÍmÍ•±˜¹…Ñ¥Ù•}Íåµ‰½±t¹¥¤(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€Í•±˜¹}™¥ÉÍÑ}‘•±¥Ù•Éå}™…¥±}±½Í” (€€€€€€€€€€€€€€€€€€€€‰=II}I)Q}]!%1}9=91Pˆ°(€€€€€€€€€€€€€€€€€€€ì‰•Ù•¹ÐˆèÍÑÈ¡•Ù•¹Ð¥ô°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€Í•±˜¹}É•±•…Í•}¥™}Ñ•Éµ¥¹…°¡¥¹Ð¡•Ù•¹Ð¹ÑÍ}•Ù•¹Ð¤°€‰=II}I)Qˆ¤œœœ°(€€€€€€€±…‰•°ô‰™¥ÉÍÐµ‘•±¥Ù•ÉäµÉ•©•Ñ¥½¸µ™…¥°µ±½Í”ˆ°(€€€€¤((€€€Í½ÕÉ”€ô}É•Á±…” (€€€€€€€Í½ÕÉ”°(€€€€€€€€œœœ€€€€€€€€‰É•Í½±ÕÑ¥½¹}Ñ…¥±}Õ¹É•Í½±Ù•‘}½Õ¹ÐˆèÍÕ´ (€€€€€€€€€€€¥Ñ•´¹•Ð ‰ÑåÁ”ˆ¤€ôô€‰IM=1UQ%=9}Q%1}U9IM=1Yˆ(€€€€€€€€€€€™½È¥Ñ•´¥¸•ÉÉ½ÉÌ(€€€€€€€€¤°(€€€€€€€€‰ÍÕ•ÍÍ}±…¥´ˆè…±Í”°œœœ°(€€€€€€€€œœœ€€€€€€€€‰É•Í½±ÕÑ¥½¹}Ñ…¥±}Õ¹É•Í½±Ù•‘}½Õ¹ÐˆèÍÕ´ (€€€€€€€€€€€¥Ñ•´¹•Ð ‰ÑåÁ”ˆ¤€ôô€‰IM=1UQ%=9}Q%1}U9IM=1Yˆ(€€€€€€€€€€€™½È¥Ñ•´¥¸•ÉÉ½ÉÌ(€€€€€€€€¤°(€€€€€€€€‰™¥ÉÍÑ}‘•±¥Ù•Éå}ÍÁ±¥Ñ}…Ñ¥Ù…Ñ•‘}½Õ¹ÐˆèÍÕ´ (€€€€€€€€€€€¥Ñ•´¹•Ð ‰ÑåÁ”ˆ¤€ôô€‰%IMQ}1%YIe}MA1%Q}Q%YQˆ(€€€€€€€€€€€™½È¥Ñ•´¥¸±¥™•å±”(€€€€€€€€¤°(€€€€€€€€‰™¥ÉÍÑ}‘•±¥Ù•Éå}‰…Í•±¥¹•}™…±±‰…­}½Õ¹ÐˆèÍÕ´ (€€€€€€€€€€€¥Ñ•´¹•Ð ‰ÑåÁ”ˆ¤€ôô€‰%IMQ}1%YIe}	M1%9}11	,ˆ(€€€€€€€€€€€™½È¥Ñ•´¥¸±¥™•å±”(€€€€€€€€¤°(€€€€€€€€‰™¥ÉÍÑ}‘•±¥Ù•Éå}Ñ…É•ÑÍ}ÍÕ‰µ¥ÑÑ•‘}½Õ¹ÐˆèÍÕ´ (€€€€€€€€€€€¥Ñ•´¹•Ð ‰ÑåÁ”ˆ¤€ôô€‰%IMQ}1%YIe}QIQM}MU	5%QQˆ(€€€€€€€€€€€™½È¥Ñ•´¥¸±¥™•å±”(€€€€€€€€¤°(€€€€€€€€‰™¥ÉÍÑ}‘•±¥Ù•Éå}™¥±±}½Õ¹ÐˆèÍÕ´ (€€€€€€€€€€€¥Ñ•´¹•Ð ‰ÑåÁ”ˆ¤€ôô€‰%IMQ}1%YIe}%11ˆ(€€€€€€€€€€€™½È¥Ñ•´¥¸±¥™•å±”(€€€€€€€€¤°(€€€€€€€€‰•áÑ•É¹…±}ÉÕ¹¹•É}™¥±±}½Õ¹ÐˆèÍÕ´ (€€€€€€€€€€€¥Ñ•´¹•Ð ‰ÑåÁ”ˆ¤€ôô€‰aQI91}IU99I}%11ˆ(€€€€€€€€€€€™½È¥Ñ•´¥¸±¥™•å±”(€€€€€€€€¤°(€€€€€€€€‰™¥ÉÍÑ}‘•±¥Ù•Éå}ÍÑ½Á}™¥±±}½Õ¹ÐˆèÍÕ´ (€€€€€€€€€€€¥Ñ•´¹•Ð ‰ÑåÁ”ˆ¤€ôô€‰%IMQ}1%YIe}MQ=A}%11ˆ(€€€€€€€€€€€™½È¥Ñ•´¥¸±¥™•å±”(€€€€€€€€¤°(€€€€€€€€‰™¥ÉÍÑ}‘•±¥Ù•Éå}ÍÑ½Á}É•Í¥é•}É•ÅÕ•ÍÑ}½Õ¹ÐˆèÍÕ´ (€€€€€€€€€€€¥Ñ•´¹•Ð ‰ÑåÁ”ˆ¤€ôô€‰%IMQ}1%YIe}MQ=A}IM%i}IEUMQˆ(€€€€€€€€€€€™½È¥Ñ•´¥¸±¥™•å±”(€€€€€€€€€¤°(€€€€€€€€‰™¥ÉÍÑ}‘•±¥Ù•Éå}™…¥±}±½Í•‘}½Õ¹ÐˆèÍÕ´ (€€€€€€€€€€€¥Ñ•´¹•Ð ‰ÑåÁ”ˆ¤€ôô€‰%IMQ}1%YIe}%1}1=Mˆ(€€€€€€€€€€€™½È¥Ñ•´¥¸±¥™•å±”(€€€€€€€€¤°(€€€€€€€€‰ÍÕ•ÍÍ}±…¥´ˆè…±Í”°€œœœ°(€€€€€€€±…‰•°ô‰™¥ÉÍÐµ‘•±¥Ù•Éäµµ•ÑÉ¥Ìˆ°(€€€€¤((€€€É•ÅÕ¥É•€ôì(€€€€€€€€‰™¥ÉÍÐµ‘•±¥Ù•ÉäµÍ¥¹±”µÁ…É•¹Ðˆè€Ä°(€€€€€€€€‰%IMQ}1%YIe}MA1%Q}Q%YQˆè€È°(€€€€€€€€‰%IMQ}1%YIe}	M1%9}11	,ˆè€È°(€€€€€€€€‰%IMQ}1%YIe}QIQM}MU	5%QQˆè€È°(€€€€€€€€‰%IMQ}1%YIe}%11ˆè€È°(€€€€€€€€‰aQI91}IU99I}%11ˆè€È°(€€€€€€€€‰%IMQ}1%YIe}MQ=A}%11ˆè€È°(€€€€€€€€‰%IMQ}1%YIe}%1}1=Mˆè€È°(€€€€€€€€‰™¥ÉÍÑ}‘•±¥Ù•Éå}ÁÉ¥µ…Éå}ÅÕ…¹Ñ¥Ñäˆè€Ä°(€€€€€€€€‰M1}%99%9}%IMQ}1%YIe}aQI91}IU99Hˆè€Ä°(€€€ô(€€€‰…€ôì(€€€€€€€Ñ½­•¸è€¡Í½ÕÉ”¹½Õ¹Ð¡Ñ½­•¸¤°•áÁ•Ñ•¤(€€€€€€€™½ÈÑ½­•¸°•áÁ•Ñ•¥¸É•ÅÕ¥É•¹¥Ñ•µÌ ¤(€€€€€€€¥˜Í½ÕÉ”¹½Õ¹Ð¡Ñ½­•¸¤€„ô•áÁ•Ñ•(€€€ô(€€€¥˜‰…è(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È¡˜‰™¥ÉÍÐµ‘•±¥Ù•ÉäÉ½ÕÑ•ÌÝ•É”¹½Ðµ…Ñ•É¥…±¥é•èí‰…‘ôˆ¤(€€€¥˜Í½ÕÉ”¹½Õ¹Ð ‰Í•±˜¹ÍÕ‰µ¥Ñ}½É‘•É}±¥ÍÐ¡½É‘•É}±¥ÍÐ¤ˆ¤€„ô€Äè(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‰‰…Í•±¥¹”™…±±‰…¬‰É…­•ÐÝ…Ì¹½ÐÉ•Ñ…¥¹••á…Ñ±ä½¹”ˆ¤(€€€É•ÑÕÉ¸Í½ÕÉ”