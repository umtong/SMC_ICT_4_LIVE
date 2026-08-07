#!/usr/bin/env python3
"""Idempotently harden ADOM's passive-fill rejection lifecycle.

This patch changes no market signal, entry price, stop, target, cost, risk,
fill probability, or evaluation period. It only classifies and serializes
known late order-rejection races which occur after a passive entry has already
opened and a fail-safe flatten is underway.
"""

from __future__ import annotations

from pathlib import Path


OLD = '''    def _handle_order_failure(self, event: Any, code: str) -> None:
        reason_text = str(getattr(event, "reason", "unknown"))
        trade = self._active_trade
        if trade is None:
            self.errors.append(f"{code} without active trade: {reason_text}")
            self._entry_inflight = False
            return
        scenario_id = trade["scenario_id"]
        state = self._scenario_states.get(scenario_id, "UNKNOWN")
        if state == "POSITION":
            self.errors.append(f"protective order failed while position open: {code}: {reason_text}")
            trade["forced_exit_reason"] = "PROTECTION_FAILURE"
            if not self._exit_inflight:
                self._exit_inflight = True
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(self.config.instrument_id)
            return
        if state == "ORDER_SUBMITTED":
            self._record_external_transition(
                scenario_id=scenario_id,
                previous_state="ORDER_SUBMITTED",
                next_state="RESET",
                reason=code,
                ts_ns=int(event.ts_event),
                reference_price=None,
                details={"venue_reason": reason_text},
            )
        self.errors.append(f"{code}: {reason_text}")
        self._active_trade = None
        self._entry_inflight = False
        self._exit_inflight = False
'''

NEW = '''    @staticmethod
    def _adom_rejection_kind(reason_text: str) -> str | None:
        normalized = reason_text.upper()
        if "STOP_MARKET" in normalized and "WAS IN THE MARKET" in normalized:
            return "PASSIVE_FILL_STOP_ALREADY_CROSSED"
        if "CONTINGENT ORDER" in normalized and "ALREADY CLOSED" in normalized:
            return "CONTINGENT_ALREADY_CLOSED_DURING_ABORT"
        if (
            "REDUCE_ONLY MARKET" in normalized
            and "WOULD HAVE INCREASED POSITION" in normalized
        ):
            return "STALE_REDUCE_ONLY_FLATTEN_AFTER_FLAT"
        return None

    def _record_adom_expected_rejection(self, kind: str, reason_text: str) -> None:
        counts = self.diagnostics.setdefault("adom_expected_execution_rejections", {})
        counts[kind] = int(counts.get(kind, 0)) + 1
        self.diagnostics.setdefault("adom_expected_execution_rejection_details", []).append(
            {"kind": kind, "reason": reason_text},
        )

    def _handle_order_failure(self, event: Any, code: str) -> None:
        reason_text = str(getattr(event, "reason", "unknown"))
        kind = self._adom_rejection_kind(reason_text)
        trade = self._active_trade
        if trade is None:
            if (
                code == "ORDER_REJECTED"
                and kind == "STALE_REDUCE_ONLY_FLATTEN_AFTER_FLAT"
                and self.portfolio.is_flat(self.config.instrument_id)
            ):
                self._record_adom_expected_rejection(kind, reason_text)
                self._entry_inflight = False
                self._exit_inflight = False
                return
            self.errors.append(f"{code} without active trade: {reason_text}")
            self._entry_inflight = False
            return

        scenario_id = trade["scenario_id"]
        state = self._scenario_states.get(scenario_id, "UNKNOWN")
        passive_entry = str(trade.get("entry_execution_mode", "")).upper() == "DEFENSE_ORIGIN_LIMIT"
        partial_abort = bool(trade.get("partial_entry_abort_requested"))

        if state == "POSITION" and passive_entry:
            if kind == "PASSIVE_FILL_STOP_ALREADY_CROSSED":
                # A resting mitigation entry can fill while the same bar has
                # already crossed the structural stop. Treat this as an
                # immediate stop/abort outcome, not as an engine fault. Do not
                # submit a second flatten if the partial-fill invariant handler
                # has already started one.
                self._record_adom_expected_rejection(kind, reason_text)
                trade["passive_fill_stop_crossed_during_activation"] = True
                if not trade.get("forced_exit_reason"):
                    trade["forced_exit_reason"] = "PASSIVE_FILL_STOP_ALREADY_CROSSED"
                if not self._exit_inflight:
                    self._exit_inflight = True
                    self.cancel_all_orders(self.config.instrument_id)
                    self.close_all_positions(self.config.instrument_id)
                return
            if (
                kind == "CONTINGENT_ALREADY_CLOSED_DURING_ABORT"
                and (partial_abort or self._exit_inflight)
            ):
                self._record_adom_expected_rejection(kind, reason_text)
                return
            if (
                kind == "STALE_REDUCE_ONLY_FLATTEN_AFTER_FLAT"
                and self._exit_inflight
                and self.portfolio.is_flat(self.config.instrument_id)
            ):
                self._record_adom_expected_rejection(kind, reason_text)
                return

        if state == "POSITION":
            self.errors.append(f"protective order failed while position open: {code}: {reason_text}")
            trade["forced_exit_reason"] = "PROTECTION_FAILURE"
            if not self._exit_inflight:
                self._exit_inflight = True
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(self.config.instrument_id)
            return
        if state == "ORDER_SUBMITTED":
            self._record_external_transition(
                scenario_id=scenario_id,
                previous_state="ORDER_SUBMITTED",
                next_state="RESET",
                reason=code,
                ts_ns=int(event.ts_event),
                reference_price=None,
                details={"venue_reason": reason_text},
            )
        self.errors.append(f"{code}: {reason_text}")
        self._active_trade = None
        self._entry_inflight = False
        self._exit_inflight = False
'''


def main() -> int:
    path = Path(__file__).resolve().with_name("nautilus_execution.py")
    text = path.read_text(encoding="utf-8")
    if "def _adom_rejection_kind" in text:
        return 0
    if OLD not in text:
        raise RuntimeError("ADOM order-failure anchor changed; refusing ambiguous repair")
    path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
