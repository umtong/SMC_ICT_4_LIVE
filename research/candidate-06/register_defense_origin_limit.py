#!/usr/bin/env python3
"""Idempotently register ADOM native limit-entry execution and lifecycle."""

from __future__ import annotations

from pathlib import Path


EXECUTION_IMPORT_A = "from decimal import Decimal, ROUND_DOWN\n"
EXECUTION_IMPORT_A_PATCHED = "from datetime import datetime, timezone\nfrom decimal import Decimal, ROUND_DOWN\n"
EXECUTION_IMPORT_B = "from entry_confirmation import DefenseCheck, continuation_defense_passes\n"
EXECUTION_IMPORT_B_PATCHED = (
    "from defense_origin_limit import resolve_entry_placement\n"
    "from entry_confirmation import DefenseCheck, continuation_defense_passes\n"
)
EXECUTION_IMPORT_C = "from nautilus_trader.model.enums import OrderSide, TimeInForce\n"
EXECUTION_IMPORT_C_PATCHED = (
    "from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce\n"
)

PLACEMENT_ANCHOR = '''        entry = float(snapshot.observation.close)
        stop = float(signal.stop_price)
        target = float(signal.target_price)
        direction = signal.direction
'''
PLACEMENT_PATCH = '''        confirmation_passed = bool(confirmation_details.get("passed", True))
        placement = resolve_entry_placement(
            original_signal,
            signal,
            snapshot,
            self._logic_params,
            confirmation_passed=confirmation_passed,
            trap_armed=trap_armed,
        )
        if reason is None and placement.reason is not None:
            reason = placement.reason
        confirmation_details["entry_placement"] = dict(placement.details)
        confirmation_details["entry_execution_mode"] = placement.mode

        entry = float(placement.expected_entry)
        stop = float(signal.stop_price)
        target = float(signal.target_price)
        direction = signal.direction
'''

ORDER_ANCHOR = '''        try:
            order_list = self.order_factory.bracket(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=quantity,
                time_in_force=TimeInForce.GTC,
                entry_post_only=False,
                entry_tags=tags,
                tp_price=target_price,
                tp_post_only=True,
                tp_tags=tags,
                sl_trigger_price=stop_price,
                sl_tags=tags,
            )
            self._active_trade = {
'''
ORDER_PATCH = '''        try:
            bracket_kwargs: dict[str, Any] = {
                "instrument_id": self.config.instrument_id,
                "order_side": side,
                "quantity": quantity,
                "entry_tags": tags,
                "tp_price": target_price,
                "tp_post_only": True,
                "tp_tags": tags,
                "sl_trigger_price": stop_price,
                "sl_tags": tags,
            }
            if placement.order_type == "LIMIT":
                if placement.expiry_ts_ns is None:
                    raise RuntimeError("limit placement missing causal expiry")
                entry_price = self._instrument.make_price(Decimal(str(entry)))
                bracket_kwargs.update(
                    {
                        "entry_order_type": OrderType.LIMIT,
                        "entry_price": entry_price,
                        "time_in_force": TimeInForce.GTD,
                        "expire_time": datetime.fromtimestamp(
                            placement.expiry_ts_ns / 1_000_000_000,
                            tz=timezone.utc,
                        ),
                        "entry_post_only": True,
                    },
                )
            else:
                bracket_kwargs.update(
                    {
                        "time_in_force": TimeInForce.GTC,
                        "entry_post_only": False,
                    },
                )
            order_list = self.order_factory.bracket(**bracket_kwargs)
            entry_order = order_list.orders[0]
            self._active_trade = {
'''

ACTIVE_FIELD_ANCHOR = '''                "expected_entry_price": entry,
                "stop_price": float(stop_price),
'''
ACTIVE_FIELD_PATCH = '''                "expected_entry_price": entry,
                "entry_execution_mode": placement.mode,
                "entry_order_type": placement.order_type,
                "entry_expiry_ts_ns": placement.expiry_ts_ns,
                "entry_client_order_id": str(entry_order.client_order_id),
                "stop_price": float(stop_price),
'''

REASON_ANCHOR = '''                reason=(
                    "FAILED_ACCEPTANCE_TRAP_MARKET_ENTRY_WITH_STRUCTURAL_BRACKET"
                    if trap_armed
                    else "DELAYED_MARKET_ENTRY_WITH_STRUCTURAL_BRACKET"
                ),
'''
REASON_PATCH = '''                reason=(
                    "FAILED_ACCEPTANCE_TRAP_MARKET_ENTRY_WITH_STRUCTURAL_BRACKET"
                    if trap_armed
                    else (
                        "CONFIRMED_DEFENSE_ORIGIN_LIMIT_WITH_STRUCTURAL_BRACKET"
                        if placement.order_type == "LIMIT"
                        else "DELAYED_MARKET_ENTRY_WITH_STRUCTURAL_BRACKET"
                    )
                ),
'''

DETAIL_ANCHOR = '''                    "favorable_drift_guard_enabled": enforce_drift_guard,
                    **confirmation_details,
'''
DETAIL_PATCH = '''                    "favorable_drift_guard_enabled": enforce_drift_guard,
                    "entry_execution_mode": placement.mode,
                    "entry_order_type": placement.order_type,
                    "entry_expiry_ts_ns": placement.expiry_ts_ns,
                    **confirmation_details,
'''

FINALIZE_ANCHOR = '''        aborted = self._scenario_engine.abort_active(snapshot, "EVALUATION_BOUNDARY")
        self._record_transitions(aborted.transitions, snapshot.observation.ts_ns)
        if not self.portfolio.is_flat(self.config.instrument_id) and not self._exit_inflight:
'''
FINALIZE_PATCH = '''        aborted = self._scenario_engine.abort_active(snapshot, "EVALUATION_BOUNDARY")
        self._record_transitions(aborted.transitions, snapshot.observation.ts_ns)
        if self._entry_inflight and self.portfolio.is_flat(self.config.instrument_id):
            self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id) and not self._exit_inflight:
'''

LIFECYCLE_ANCHOR = '''class NautilusLifecycleMixin:
    """Translate Nautilus events into causal scenario and trade records."""

    def on_position_opened(self, event: Any) -> None:
'''
LIFECYCLE_PATCH = '''class NautilusLifecycleMixin:
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
'''

POSITION_OPENED_TAIL_ANCHOR = '''        self._sample_equity(int(event.ts_event))

    def on_position_closed(self, event: Any) -> None:
'''
POSITION_OPENED_TAIL_PATCH = '''        self._abort_partial_entry_if_needed(event.quantity)
        self._sample_equity(int(event.ts_event))

    def on_position_closed(self, event: Any) -> None:
'''


def _replace_once(text: str, anchor: str, patch: str, label: str) -> str:
    if patch in text:
        return text
    if anchor not in text:
        raise RuntimeError(f"{label} anchor changed; refusing ambiguous patch")
    return text.replace(anchor, patch, 1)


def main() -> int:
    candidate_dir = Path(__file__).resolve().parent
    execution_path = candidate_dir / "nautilus_execution.py"
    lifecycle_path = candidate_dir / "nautilus_lifecycle.py"

    execution = execution_path.read_text(encoding="utf-8")
    execution = _replace_once(
        execution,
        EXECUTION_IMPORT_A,
        EXECUTION_IMPORT_A_PATCHED,
        "datetime import",
    )
    execution = _replace_once(
        execution,
        EXECUTION_IMPORT_B,
        EXECUTION_IMPORT_B_PATCHED,
        "placement import",
    )
    execution = _replace_once(
        execution,
        EXECUTION_IMPORT_C,
        EXECUTION_IMPORT_C_PATCHED,
        "order type import",
    )
    execution = _replace_once(execution, PLACEMENT_ANCHOR, PLACEMENT_PATCH, "placement")
    execution = _replace_once(execution, ORDER_ANCHOR, ORDER_PATCH, "order construction")
    execution = _replace_once(
        execution,
        ACTIVE_FIELD_ANCHOR,
        ACTIVE_FIELD_PATCH,
        "active trade fields",
    )
    execution = _replace_once(execution, REASON_ANCHOR, REASON_PATCH, "transition reason")
    execution = _replace_once(execution, DETAIL_ANCHOR, DETAIL_PATCH, "transition details")
    execution = _replace_once(execution, FINALIZE_ANCHOR, FINALIZE_PATCH, "finalization")
    execution_path.write_text(execution, encoding="utf-8")

    lifecycle = lifecycle_path.read_text(encoding="utf-8")
    lifecycle = _replace_once(
        lifecycle,
        LIFECYCLE_ANCHOR,
        LIFECYCLE_PATCH,
        "limit entry lifecycle",
    )
    lifecycle = _replace_once(
        lifecycle,
        POSITION_OPENED_TAIL_ANCHOR,
        POSITION_OPENED_TAIL_PATCH,
        "partial entry invariant abort",
    )
    lifecycle_path.write_text(lifecycle, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
