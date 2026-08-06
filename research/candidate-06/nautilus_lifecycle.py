"""Nautilus position and order lifecycle callbacks for candidate-06."""

from __future__ import annotations

from typing import Any

from excursion_diagnostics import calculate_excursion_diagnostics
from nautilus_strategy_common import money_to_float as _money_to_float, utc_day as _utc_day, utc_hour as _utc_hour


class NautilusLifecycleMixin:
    """Translate Nautilus events into causal scenario and trade records."""

    def _request_partial_entry_flatten(
        self,
        trigger: str,
        *,
        allow_repeat: bool = False,
    ) -> None:
        """Request a reduce-only flatten after a partial-entry invariant breach."""

        trade = self._active_trade
        if trade is None or not trade.get("partial_entry_abort_requested"):
            return
        if self.portfolio.is_flat(self.config.instrument_id):
            return
        attempts = int(trade.get("partial_entry_flatten_attempts", 0))
        if attempts > 0 and not allow_repeat:
            return
        trade["partial_entry_flatten_attempts"] = attempts + 1
        counts = self.diagnostics.setdefault("partial_entry_flatten_requests", {})
        counts[trigger] = int(counts.get(trigger, 0)) + 1
        self.close_all_positions(self.config.instrument_id)

    def _handle_unfilled_entry_terminal(self, event: Any, code: str) -> None:
        trade = self._active_trade
        if trade is None:
            return
        expected = trade.get("entry_client_order_id")
        actual = str(getattr(event, "client_order_id", ""))
        if expected is None or actual != str(expected):
            return
        scenario_id = trade["scenario_id"]
        state = self._scenario_states.get(scenario_id, "UNKNOWN")
        if state == "POSITION":
            if trade.get("partial_entry_abort_requested"):
                counts = self.diagnostics.setdefault(
                    "partial_entry_parent_terminal_counts",
                    {},
                )
                counts[code] = int(counts.get(code, 0)) + 1
                trade["partial_entry_parent_terminal_code"] = code
                self._request_partial_entry_flatten("PARENT_TERMINAL")
            return
        counts = self.diagnostics.setdefault("unfilled_entry_terminal_counts", {})
        counts[code] = int(counts.get(code, 0)) + 1
        if state == "ORDER_SUBMITTED":
            self._record_external_transition(
                scenario_id=scenario_id,
                previous_state="ORDER_SUBMITTED",
                next_state="RESET",
                reason=code,
                ts_ns=int(event.ts_event),
                reference_price=trade.get("expected_entry_price"),
                details={
                    "entry_order_type": trade.get("entry_order_type"),
                    "entry_execution_mode": trade.get("entry_execution_mode"),
                    "entry_expiry_ts_ns": trade.get("entry_expiry_ts_ns"),
                },
            )
        self._active_trade = None
        self._entry_inflight = False
        self._exit_inflight = False

    def _abort_partial_entry_if_needed(self, opened_quantity: Any) -> None:
        """Flatten a partial passive entry instead of carrying entry plus position.

        Nautilus bracket parents are OTO orders.  Canceling only a partially
        filled parent through the generic strategy API cascades to its linked
        stop and target, so the safe invariant response is: mark the trade as an
        execution abort, cancel all remaining orders, and flatten the native
        position.  This changes no signal, stop, target, risk, or alpha rule.
        """

        trade = self._active_trade
        if trade is None or trade.get("partial_entry_abort_requested"):
            return
        expected = trade.get("entry_client_order_id")
        if expected is None:
            return
        matches = [
            order
            for order in self.cache.orders_open(instrument_id=self.config.instrument_id)
            if str(order.client_order_id) == str(expected)
        ]
        if not matches:
            return
        if len(matches) > 1:
            self.errors.append(
                f"duplicate open parent entry while aborting partial fill: {expected}",
            )
        trade["partial_entry_abort_requested"] = True
        trade["partial_entry_abort_opened_quantity"] = str(opened_quantity)
        trade["partial_entry_open_parent_count"] = len(matches)
        trade["partial_entry_flatten_attempts"] = 0
        trade["partial_entry_cancel_retry_count"] = 0
        trade["forced_exit_reason"] = "PARTIAL_ENTRY_SINGLE_SLOT_ABORT"
        counts = self.diagnostics.setdefault("partial_entry_abort_counts", {})
        mode = str(trade.get("entry_execution_mode", "UNKNOWN"))
        counts[mode] = int(counts.get(mode, 0)) + 1
        self._exit_inflight = True
        self.cancel_all_orders(self.config.instrument_id)
        self._request_partial_entry_flatten("INITIAL_ABORT")

    def on_order_expired(self, event: Any) -> None:
        self._handle_unfilled_entry_terminal(event, "UNFILLED_ENTRY_EXPIRED")

    def on_order_canceled(self, event: Any) -> None:
        self._handle_unfilled_entry_terminal(event, "UNFILLED_ENTRY_CANCELED")

    def on_order_cancel_rejected(self, event: Any) -> None:
        trade = self._active_trade
        if trade is None or not trade.get("partial_entry_abort_requested"):
            return
        expected = trade.get("entry_client_order_id")
        actual = str(getattr(event, "client_order_id", ""))
        if expected is None or actual != str(expected):
            return
        reason = str(getattr(event, "reason", "unknown"))
        counts = self.diagnostics.setdefault("partial_entry_cancel_rejections", {})
        counts[reason] = int(counts.get(reason, 0)) + 1
        self.errors.append(f"partial-entry parent cancel rejected: {reason}")
        retries = int(trade.get("partial_entry_cancel_retry_count", 0))
        if retries < 1:
            trade["partial_entry_cancel_retry_count"] = retries + 1
            self.cancel_all_orders(self.config.instrument_id)
        self._request_partial_entry_flatten(
            "PARENT_CANCEL_REJECTED",
            allow_repeat=True,
        )

    def on_position_changed(self, event: Any) -> None:
        trade = self._active_trade
        if trade is None or not trade.get("partial_entry_abort_requested"):
            return
        quantity = str(getattr(event, "quantity", ""))
        previous = str(trade.get("partial_entry_abort_opened_quantity", ""))
        if quantity == previous:
            return
        trade["partial_entry_abort_opened_quantity"] = quantity
        self._request_partial_entry_flatten(
            "POSITION_CHANGED_AFTER_ABORT",
            allow_repeat=True,
        )

    def on_position_opened(self, event: Any) -> None:
        if self._active_trade is None:
            self.errors.append(f"position opened without active trade: {event}")
            return
        self._entry_inflight = False
        self._active_trade["actual_entry_price"] = float(event.avg_px_open)
        self._active_trade["opened_ts_ns"] = int(event.ts_event)
        self._active_trade["opened_bar_index"] = self._bar_index
        self._record_external_transition(
            scenario_id=self._active_trade["scenario_id"],
            previous_state="ORDER_SUBMITTED",
            next_state="POSITION",
            reason="ENTRY_FILLED",
            ts_ns=int(event.ts_event),
            reference_price=float(event.avg_px_open),
            details={
                "quantity": str(event.quantity),
                "planned_loss_budget": self._active_trade["planned_loss_budget"],
                "loss_per_unit": self._active_trade["loss_per_unit"],
            },
        )
        self._abort_partial_entry_if_needed(event.quantity)
        self._sample_equity(int(event.ts_event))

    def on_position_closed(self, event: Any) -> None:
        trade = self._active_trade
        if trade is None:
            self.errors.append(f"position closed without active trade: {event}")
            self._entry_inflight = False
            self._exit_inflight = False
            return
        pnl = _money_to_float(event.realized_pnl)
        close_price = float(event.avg_px_close)
        tick = float(self._instrument.price_increment)
        closed_ts_ns = int(event.ts_event)
        excursion = calculate_excursion_diagnostics(
            trade,
            self._observations.values(),
            closed_ts_ns=closed_ts_ns,
            tick=tick,
        )
        forced = trade.get("forced_exit_reason")
        if forced:
            outcome = str(forced)
        elif trade["direction"] == "LONG":
            if close_price >= float(trade["target_price"]) - tick:
                outcome = "TARGET"
            elif close_price <= float(trade["stop_price"]) + tick:
                outcome = "STOP"
            else:
                outcome = "OTHER_EXIT"
        else:
            if close_price <= float(trade["target_price"]) + tick:
                outcome = "TARGET"
            elif close_price >= float(trade["stop_price"]) - tick:
                outcome = "STOP"
            else:
                outcome = "OTHER_EXIT"
        planned_budget = float(trade["planned_loss_budget"])
        record = {
            **trade,
            **excursion,
            "actual_entry_price": float(event.avg_px_open),
            "actual_exit_price": close_price,
            "closed_ts_ns": closed_ts_ns,
            "closed_day_utc": _utc_day(closed_ts_ns),
            "closed_hour_utc": _utc_hour(closed_ts_ns),
            "duration_minutes": float(event.duration_ns) / 60_000_000_000,
            "realized_pnl_after_cost": pnl,
            "realized_return": float(event.realized_return),
            "realized_r_multiple": pnl / planned_budget if planned_budget > 0.0 else 0.0,
            "outcome": outcome,
        }
        self.closed_trades.append(record)
        self._record_external_transition(
            scenario_id=trade["scenario_id"],
            previous_state="POSITION",
            next_state=outcome,
            reason=f"POSITION_CLOSED_{outcome}",
            ts_ns=closed_ts_ns,
            reference_price=close_price,
            details={
                "realized_pnl_after_cost": pnl,
                "realized_r_multiple": record["realized_r_multiple"],
                "duration_minutes": record["duration_minutes"],
                "mfe_close_net_r_after_cost": record["mfe_close_net_r_after_cost"],
                "mfe_intrabar_net_r_after_cost": record["mfe_intrabar_net_r_after_cost"],
                "mae_stop_units": record["mae_stop_units"],
            },
        )
        self._active_trade = None
        self._entry_inflight = False
        self._exit_inflight = False
        self._sample_equity(closed_ts_ns)

    def on_order_denied(self, event: Any) -> None:
        self.diagnostics["order_denials"] += 1
        self._handle_order_failure(event, "ORDER_DENIED")

    def on_order_rejected(self, event: Any) -> None:
        self.diagnostics["order_rejections"] += 1
        self._handle_order_failure(event, "ORDER_REJECTED")

    def on_stop(self) -> None:
        # The runner supplies a final boundary bar and expects the strategy
        # to be flat before engine shutdown. This hook is a last-resort guard.
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.errors.append("engine stopped with an open position")
