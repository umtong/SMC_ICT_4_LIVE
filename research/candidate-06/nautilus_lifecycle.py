"""Nautilus position and order lifecycle callbacks for candidate-06."""

from __future__ import annotations

from typing import Any

from nautilus_strategy_common import money_to_float as _money_to_float, utc_day as _utc_day, utc_hour as _utc_hour


class NautilusLifecycleMixin:
    """Translate Nautilus events into causal scenario and trade records."""

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
            "actual_entry_price": float(event.avg_px_open),
            "actual_exit_price": close_price,
            "closed_ts_ns": int(event.ts_event),
            "closed_day_utc": _utc_day(int(event.ts_event)),
            "closed_hour_utc": _utc_hour(int(event.ts_event)),
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
            ts_ns=int(event.ts_event),
            reference_price=close_price,
            details={
                "realized_pnl_after_cost": pnl,
                "realized_r_multiple": record["realized_r_multiple"],
                "duration_minutes": record["duration_minutes"],
            },
        )
        self._active_trade = None
        self._entry_inflight = False
        self._exit_inflight = False
        self._sample_equity(int(event.ts_event))

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

