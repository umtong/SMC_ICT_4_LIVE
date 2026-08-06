from __future__ import annotations

import unittest

import filter_candidate_signals as candidate


class CandidateRouteFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            {"scenario": "INFORMED_INVENTORY_PULLBACK_CONTINUATION"},
            {"scenario": "POST_ATTACK_LIQUIDATION_ABSORPTION_REVERSAL"},
            {"scenario": "POST_ATTACK_TRAPPED_INVENTORY_REVERSAL"},
            {"scenario": "MICRO_BALANCE_NEW_INVENTORY_RETEST_CONTINUATION"},
            {"scenario": "MICRO_BALANCE_TRAPPED_BREAKOUT_REVERSAL"},
        ]

    def test_full_route_preserves_all_rows_and_order(self) -> None:
        selected = candidate.filter_rows(self.rows, "v34", "full")
        self.assertEqual(selected, self.rows)
        self.assertIsNot(selected, self.rows)

    def test_v34_reversal_route_keeps_both_inventory_causes(self) -> None:
        selected = candidate.filter_rows(self.rows, "v34", "reversal")
        self.assertEqual(
            [row["scenario"] for row in selected],
            [
                "POST_ATTACK_LIQUIDATION_ABSORPTION_REVERSAL",
                "POST_ATTACK_TRAPPED_INVENTORY_REVERSAL",
            ],
        )

    def test_v36_continuation_and_reversal_are_disjoint(self) -> None:
        continuation = candidate.filter_rows(self.rows, "v36", "continuation")
        reversal = candidate.filter_rows(self.rows, "v36", "reversal")
        continuation_names = {row["scenario"] for row in continuation}
        reversal_names = {row["scenario"] for row in reversal}
        self.assertFalse(continuation_names & reversal_names)
        self.assertEqual(
            continuation_names,
            {"MICRO_BALANCE_NEW_INVENTORY_RETEST_CONTINUATION"},
        )
        self.assertEqual(
            reversal_names,
            {"MICRO_BALANCE_TRAPPED_BREAKOUT_REVERSAL"},
        )

    def test_unsupported_route_is_not_silently_empty(self) -> None:
        with self.assertRaises(ValueError):
            candidate.filter_rows(self.rows, "v35", "unknown")


if __name__ == "__main__":
    unittest.main()
