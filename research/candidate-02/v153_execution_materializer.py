#!/usr/bin/env python3
"""Execution-safety overlay for Candidate-02 V153/V154.

The pinned Candidate 13 V9 portfolio runner is reused rather than rebuilt. Its
quarter-hour source materializer is applied first, then the only execution change
needed by the V9 failure evidence is installed: if a contingent protective child
is denied or rejected after the parent has opened a position, the strategy
cancels remaining orders and immediately submits a flatten request. A rejection
while flat remains an engine error and therefore fails the inherited evidence
audit. A tiny evidence-compatibility marker is also emitted for the V154
six-interval diagnostic driver; it does not participate in execution or metrics.
"""
from __future__ import annotations

import re

from quarter_hour_materializer_v9 import (
    materialize_quarter_hour_source as _materialize_v9,
)


def _replace_once(
    source: str,
    pattern: re.Pattern[str],
    replacement: str,
    *,
    label: str,
) -> str:
    updated, count = pattern.subn(replacement, source)
    if count != 1:
        raise RuntimeError(
            f"Candidate-02 V154 execution boundary drifted at {label}: "
            f"expected one match, found {count}",
        )
    return updated


def materialize_quarter_hour_source(source: str) -> str:
    """Apply the pinned V9 integration and install fail-closed protection."""
    source = _materialize_v9(source)

    handlers = re.compile(
        r'        def on_order_denied\(self, event: OrderEvent\) -> None:\n'
        r'            self\._record_order_event\(event, "ORDER_DENIED"\)\n'
        r'            self\.errors\.append\(\{"type": "ORDER_DENIED", "event": str\(event\)\}\)\n'
        r'            self\._release_if_terminal\(int\(event\.ts_event\), "ORDER_DENIED"\)\n'
        r'\n'
        r'        def on_order_rejected\(self, event: OrderEvent\) -> None:\n'
        r'            self\._record_order_event\(event, "ORDER_REJECTED"\)\n'
        r'            self\.errors\.append\(\{"type": "ORDER_REJECTED", "event": str\(event\)\}\)\n'
        r'            self\._release_if_terminal\(int\(event\.ts_event\), "ORDER_REJECTED"\)\n'
    )
    replacement = '''        def _fail_close_rejected_protective_child(
            self,
            event: OrderEvent,
            kind: str,
        ) -> bool:
            if self.active_plan is None or self.active_symbol is None:
                return False
            instrument_id = instruments[self.active_symbol].id
            if self.portfolio.is_flat(instrument_id):
                return False
            scenario_id = self.active_plan.scenario_id
            if getattr(self, "_protective_fail_close_scenario_id", None) == scenario_id:
                # A second rejection while the emergency close is already in
                # flight is not hidden; the caller records it as an engine error.
                return False
            self._protective_fail_close_scenario_id = scenario_id
            ts_ns = int(event.ts_event)
            record = {
                "type": "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED",
                "rejection_kind": kind,
                "ts_event": ts_ns,
                "scenario_id": scenario_id,
                "symbol": self.active_symbol,
                "client_order_id": str(event.client_order_id),
                "event": str(event),
            }
            self.lifecycle.append(record)
            if self.cache.orders_open_count(
                instrument_id=instrument_id,
                strategy_id=self.id,
            ):
                self.cancel_all_orders(instrument_id)
            self.close_all_positions(instrument_id)
            return True

        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            if self._fail_close_rejected_protective_child(event, "ORDER_DENIED"):
                return
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")

        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            if self._fail_close_rejected_protective_child(event, "ORDER_REJECTED"):
                return
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")
'''
    source = _replace_once(
        source,
        handlers,
        replacement,
        label="protective-child-fail-close",
    )

    evidence_boundary = re.compile(
        r'        write_json_atomic\(output_dir / "submitted_plans\.json", \{"plans": strategy\.plans\}\)\n'
        r'        write_json_atomic\(output_dir / "order_lifecycle\.json", \{"events": strategy\.lifecycle\}\)\n'
    )
    evidence_replacement = '''        write_json_atomic(output_dir / "submitted_plans.json", {"plans": strategy.plans})
        write_json_atomic(output_dir / "order_lifecycle.json", {"events": strategy.lifecycle})
        write_json_atomic(
            output_dir / "closed_trades.json",
            {"closed_trades": metrics.get("closed_trades", 0)},
        )
'''
    source = _replace_once(
        source,
        evidence_boundary,
        evidence_replacement,
        label="closed-trade-evidence-marker",
    )

    required = (
        "QuarterHourCommonFlowEngine(logic_config)",
        "plans.append((qh_plan, qh_candidate))",
        "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED",
        "close_all_positions(instrument_id)",
        'output_dir / "closed_trades.json"',
    )
    missing = [token for token in required if token not in source]
    if missing:
        raise RuntimeError(f"Candidate-02 V154 overlay was not installed: {missing}")
    return source
