from __future__ import annotations

import unittest

from first_delivery_materializer import materialize_first_delivery_source
from runner_materializer import NEW_ORDER_BLOCK


def synthetic_source() -> str:
    return '''
            self.active_position_opened_ns: int | None = None
            self.time_exit_requested = False
            self.resolution_exit_requested = False
            self.last_ts_ns = 0

        def _release_if_terminal(self, ts_ns: int, reason: str) -> None:
            pass
                    self.active_plan = None
                    self.active_symbol = None
                    self.active_position_opened_ns = None
                    self.time_exit_requested = False
                    self.active_plan = None
                    self.active_symbol = None
                    self.active_position_opened_ns = None
                    self.time_exit_requested = False
            side = OrderSide.BUY if plan.direction == Direction.LONG else OrderSide.SELL
            try:
''' + NEW_ORDER_BLOCK + '''
                self.submit_order_list(order_list)
            except Exception as exc:
                record = {}
                self.errors.append(record)
                self.logic[symbol].mark_rejected(plan, self.last_ts_ns, record["type"], record)
                self._capture_events(symbol)
                return

            self.logic[symbol].mark_submitted(
        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_FILLED")
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")
        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")

        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")
        "resolution_tail_unresolved_count": sum(
            item.get("type") == "RESOLUTION_TAIL_UNRESOLVED"
            for item in errors
        ),
        "success_claim": False,
'''


class FirstDeliveryMaterializerTest(unittest.TestCase):
    def test_materializes_single_parent_and_baseline_fallback(self):
        result = materialize_first_delivery_source(synthetic_source())
        self.assertIn("first-delivery-single-parent", result)
        self.assertIn("FIRST_DELIVERY_SPLIT_ACTIVATED", result)
        self.assertIn("FIRST_DELIVERY_BASELINE_FALLBACK", result)
        self.assertIn("self.submit_order(entry_order)", result)
        self.assertEqual(result.count("self.submit_order_list(order_list)"), 1)
        self.assertNotIn("candidate-14-unified-parent", result)
        self.assertNotIn(".is_closed()", result)
        self.assertIn(".is_closed", result)

    def test_fails_closed_on_source_drift(self):
        with self.assertRaises(RuntimeError):
            materialize_first_delivery_source(synthetic_source().replace(
                "self.resolution_exit_requested = False",
                "self.resolution_exit_requested = True",
                1,
            ))


if __name__ == "__main__":
    unittest.main()
