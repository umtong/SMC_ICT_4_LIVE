from __future__ import annotations

import unittest

import pandas as pd

from validate_order_lifecycle import validate_lifecycle


class OrderLifecycleValidationTest(unittest.TestCase):
    def test_partial_entries_before_exit_are_one_trade(self) -> None:
        events = [
            {"kind": "submitted", "plan_id": "p1", "entry_client_order_id": "entry-1"},
            {"kind": "order_filled", "plan_id": "p1", "client_order_id": "entry-1", "role": "ENTRY"},
            {"kind": "order_filled", "plan_id": "p1", "client_order_id": "entry-1", "role": "ENTRY"},
            {"kind": "order_filled", "plan_id": "p1", "client_order_id": "target-1", "role": "EXIT_OR_PROTECTIVE"},
        ]
        audit = pd.DataFrame([{"plan_id": "p1", "position_id": "position-1"}])
        result = validate_lifecycle(events, audit)
        self.assertEqual(result["status"], "PASS")

    def test_parent_refill_after_target_is_rejected_even_if_plan_id_was_lost(self) -> None:
        events = [
            {"kind": "submitted", "plan_id": "p1", "entry_client_order_id": "entry-1"},
            {"kind": "order_filled", "plan_id": "p1", "client_order_id": "entry-1", "role": "ENTRY"},
            {"kind": "order_filled", "plan_id": "p1", "client_order_id": "target-1", "role": "EXIT_OR_PROTECTIVE"},
            {"kind": "order_filled", "plan_id": None, "client_order_id": "entry-1", "role": "EXIT_OR_PROTECTIVE"},
        ]
        audit = pd.DataFrame(
            [
                {"plan_id": "p1", "position_id": "position-1"},
                {"plan_id": "p1", "position_id": "position-2"},
            ],
        )
        result = validate_lifecycle(events, audit)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(result["entry_fill_after_exit"]), 1)
        self.assertEqual(len(result["misclassified_entry_fills"]), 1)
        self.assertEqual(result["duplicate_plan_positions"], {"p1": 2})


if __name__ == "__main__":
    unittest.main()
