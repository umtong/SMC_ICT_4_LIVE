from __future__ import annotations

import unittest

import pandas as pd

import micro_auction_balance_transition_compiler_v2 as candidate


class BalanceAlignmentTests(unittest.TestCase):
    def test_first_completed_balance_has_finite_internal_path(self) -> None:
        bars = candidate.BALANCE_BARS
        closes = [99.5 if index % 2 == 0 else 100.5 for index in range(bars)]
        data = pd.DataFrame(
            {
                "high": [101.0] * bars,
                "low": [99.0] * bars,
                "close": closes,
                "atr": [2.0] * bars,
                "metric_sum_open_interest": [100.0] * bars,
            }
        )
        metrics = candidate.build_balance_metrics(data)
        self.assertTrue(pd.notna(metrics.path_to_width.iloc[-1]))
        self.assertTrue(pd.notna(metrics.net_efficiency.iloc[-1]))
        frozen = candidate.freeze_balance(bars, bars - 1, data, metrics)
        self.assertIsNotNone(frozen)
        assert frozen is not None
        self.assertEqual(frozen.start_index, 0)
        self.assertEqual(frozen.end_index, bars - 1)
        self.assertEqual(frozen.created_index, bars)

    def test_future_outlier_is_not_inside_frozen_balance(self) -> None:
        bars = candidate.BALANCE_BARS
        closes = [99.5 if index % 2 == 0 else 100.5 for index in range(bars)]
        data = pd.DataFrame(
            {
                "high": [101.0] * bars + [500.0],
                "low": [99.0] * bars + [1.0],
                "close": closes + [400.0],
                "atr": [2.0] * (bars + 1),
                "metric_sum_open_interest": [100.0] * (bars + 1),
            }
        )
        metrics = candidate.build_balance_metrics(data)
        frozen = candidate.freeze_balance(1, bars - 1, data, metrics)
        self.assertIsNotNone(frozen)
        assert frozen is not None
        self.assertEqual(frozen.high, 101.0)
        self.assertEqual(frozen.low, 99.0)


if __name__ == "__main__":
    unittest.main()
