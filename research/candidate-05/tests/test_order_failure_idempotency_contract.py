from __future__ import annotations

from types import SimpleNamespace
import unittest

from order_failure_idempotency_contract import install
import strategy_v2


class _Harness:
    def __init__(self) -> None:
        self.current_scenario_id = "scenario-1"
        self.bars = [{"ts": 123}]
        self.diagnostics = {}
        self.calls = []


class OrderFailureIdempotencyContractTests(unittest.TestCase):
    def test_exact_duplicate_is_ignored_after_first_callback(self) -> None:
        original = strategy_v2.LiquidityResponseRetraceStrategy._order_failure
        calls = []

        def fake(self, event, event_type):
            calls.append((self.current_scenario_id, event.ts_event, event_type, str(event)))

        try:
            strategy_v2.LiquidityResponseRetraceStrategy._order_failure = fake
            install()
            wrapped = strategy_v2.LiquidityResponseRetraceStrategy._order_failure
            harness = _Harness()
            event = SimpleNamespace(ts_event=456, value="same")
            wrapped(harness, event, "ORDER_REJECTED")
            wrapped(harness, event, "ORDER_REJECTED")
            self.assertEqual(len(calls), 1)
            self.assertEqual(harness.diagnostics["duplicate_order_failure_callbacks_ignored"], 1)
        finally:
            strategy_v2.LiquidityResponseRetraceStrategy._order_failure = original

    def test_different_event_is_not_suppressed(self) -> None:
        original = strategy_v2.LiquidityResponseRetraceStrategy._order_failure
        calls = []

        def fake(self, event, event_type):
            calls.append((event.ts_event, event_type, str(event)))

        try:
            strategy_v2.LiquidityResponseRetraceStrategy._order_failure = fake
            install()
            wrapped = strategy_v2.LiquidityResponseRetraceStrategy._order_failure
            harness = _Harness()
            wrapped(harness, SimpleNamespace(ts_event=456, value="a"), "ORDER_REJECTED")
            wrapped(harness, SimpleNamespace(ts_event=457, value="b"), "ORDER_REJECTED")
            self.assertEqual(len(calls), 2)
        finally:
            strategy_v2.LiquidityResponseRetraceStrategy._order_failure = original


if __name__ == "__main__":
    unittest.main()
