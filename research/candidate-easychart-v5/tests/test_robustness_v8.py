from __future__ import annotations

import unittest

import pandas as pd

from robustness_v8 import trade_robustness_metrics


COLUMNS = [
    "position_id",
    "realized_pnl",
    "nav_at_submission",
    "actual_net_r",
    "ts_closed",
]


class RobustnessMetricsTests(unittest.TestCase):
    def test_empty_account_is_explicit(self) -> None:
        result = trade_robustness_metrics(pd.DataFrame(columns=COLUMNS))
        self.assertEqual(result["robustness_trade_count"], 0)
        self.assertEqual(result["robustness_status"], "NO_TRADES")

    def test_single_outlier_dependence_is_detected_without_reordering(self) -> None:
        frame = pd.DataFrame(
            [
                ("P1", 10.0, 100.0, 1.0, "2024-01-01T10:00:00Z"),
                ("P2", -5.5, 110.0, -0.5, "2024-01-01T11:00:00Z"),
                ("P3", 20.9, 104.5, 2.0, "2024-01-02T10:00:00Z"),
                ("P4", -12.54, 125.4, -1.0, "2024-01-03T10:00:00Z"),
            ],
            columns=COLUMNS,
        )
        result = trade_robustness_metrics(frame, reported_total_return=0.1286)
        self.assertAlmostEqual(result["trade_compounded_return"], 0.1286)
        self.assertAlmostEqual(result["trade_compound_reproduction_error"], 0.0)
        self.assertAlmostEqual(result["trade_path_max_drawdown"], -0.10)
        self.assertAlmostEqual(result["best_trade_removed_compound_return"], -0.0595)
        self.assertAlmostEqual(result["best_close_day_removed_compound_return"], -0.0595)
        self.assertFalse(result["best_trade_removed_still_profitable"])
        self.assertFalse(result["best_close_day_removed_still_profitable"])
        self.assertEqual(result["robustness_status"], "OUTLIER_DEPENDENT")
        self.assertEqual(result["maximum_consecutive_losses"], 1)
        self.assertEqual(result["positive_close_days"], 2)
        self.assertEqual(result["negative_close_days"], 1)
        self.assertAlmostEqual(result["gross_profit_top1_share"], 20.9 / 30.9)

    def test_distributed_profit_survives_best_trade_and_best_day_removal(self) -> None:
        frame = pd.DataFrame(
            [
                ("P1", 5.0, 100.0, 0.5, "2024-01-01T10:00:00Z"),
                ("P2", 4.2, 105.0, 0.4, "2024-01-02T10:00:00Z"),
                ("P3", -2.184, 109.2, -0.2, "2024-01-03T10:00:00Z"),
                ("P4", 3.21048, 107.016, 0.3, "2024-01-04T10:00:00Z"),
            ],
            columns=COLUMNS,
        )
        result = trade_robustness_metrics(frame, reported_total_return=0.1022648)
        self.assertAlmostEqual(result["trade_compounded_return"], 0.1022648)
        self.assertGreater(result["best_trade_removed_compound_return"], 0.0)
        self.assertGreater(result["best_close_day_removed_compound_return"], 0.0)
        self.assertTrue(result["best_trade_removed_still_profitable"])
        self.assertTrue(result["best_close_day_removed_still_profitable"])
        self.assertEqual(result["robustness_status"], "NON_SINGLE_OUTLIER_PROFIT")
        self.assertEqual(result["robustness_trade_count"], 4)
        self.assertAlmostEqual(result["win_rate"], 0.75)
        self.assertAlmostEqual(result["median_net_r"], 0.35)

    def test_invalid_account_contract_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "position_id"):
            trade_robustness_metrics(pd.DataFrame(columns=COLUMNS[1:]))

        bad_nav = pd.DataFrame(
            [("P1", 1.0, 0.0, 0.1, "2024-01-01T10:00:00Z")],
            columns=COLUMNS,
        )
        with self.assertRaisesRegex(ValueError, "non-positive NAV"):
            trade_robustness_metrics(bad_nav)

        account_destroyed = pd.DataFrame(
            [("P1", -100.0, 100.0, -10.0, "2024-01-01T10:00:00Z")],
            columns=COLUMNS,
        )
        with self.assertRaisesRegex(ValueError, "factor is non-positive"):
            trade_robustness_metrics(account_destroyed)


if __name__ == "__main__":
    unittest.main()
