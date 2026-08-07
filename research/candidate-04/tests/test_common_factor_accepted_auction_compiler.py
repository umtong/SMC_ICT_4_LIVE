"""Focused causal tests for candidate-04 V52."""
from __future__ import annotations

import unittest

import pandas as pd

import common_factor_accepted_auction_compiler as v52


def frame(rows: list[dict[str, float]]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(rows), freq="min", tz="UTC")
    return pd.DataFrame(rows, index=index)


class V52Tests(unittest.TestCase):
    def test_current_outlier_does_not_scale_itself(self) -> None:
        values = pd.Series([1.0] * 720 + [1_000_000.0])
        result = v52.shifted_quantile(values, 0.70)
        self.assertEqual(float(result.iloc[-1]), 1.0)

    def test_common_factor_requires_breadth_not_btc_alone(self) -> None:
        index = pd.date_range("2024-01-01", periods=1, freq="min", tz="UTC")
        factors = {
            "normalized_return": {
                "BTCUSDT": pd.Series([1.2], index=index),
                "ETHUSDT": pd.Series([-0.2], index=index),
                "SOLUSDT": pd.Series([-0.2], index=index),
                "XRPUSDT": pd.Series([-0.2], index=index),
            },
            "normalized_flow": {
                "BTCUSDT": pd.Series([1.0], index=index),
                "ETHUSDT": pd.Series([-0.2], index=index),
                "SOLUSDT": pd.Series([-0.2], index=index),
                "XRPUSDT": pd.Series([-0.2], index=index),
            },
            "common_return": pd.Series([-0.2], index=index),
            "common_flow": pd.Series([-0.2], index=index),
        }
        passed, details = v52.common_factor_acceptance(
            factors,
            0,
            1,
            minimum_breadth=3,
            minimum_common_return=0.35,
            minimum_common_flow=0.20,
        )
        self.assertFalse(passed)
        self.assertEqual(details["common_factor_breadth"], 1)

    def test_state_oi_creation_uses_event_interval_not_trailing_sign(self) -> None:
        data = frame(
            [
                {"metric_sum_open_interest": 100.0},
                {"metric_sum_open_interest": 100.0},
                {"metric_sum_open_interest": 101.0},
                {"metric_sum_open_interest": 102.0},
            ]
        )
        passed, change = v52.state_oi_creation(data, 1, 3, 0.01)
        self.assertTrue(passed)
        self.assertAlmostEqual(change, 0.02)
        passed, _ = v52.state_oi_creation(data, 1, 3, 0.03)
        self.assertFalse(passed)

    def test_acceptance_requires_two_completed_outside_closes_and_index_proxy(self) -> None:
        data = frame(
            [
                {"close": 101.0, "atr": 1.0, "ret_60s_bps": 4.0, "basis_change_5m": 1.0},
                {"close": 101.1, "atr": 1.0, "ret_60s_bps": 3.0, "basis_change_5m": 1.0},
                {"close": 101.2, "atr": 1.0, "ret_60s_bps": 2.0, "basis_change_5m": 0.5},
            ]
        )
        end, details = v52.classify_outside_acceptance(data, 0, 100.0, 1)
        self.assertEqual(end, 2)
        self.assertEqual(details["outside_close_count"], 3)
        data.iloc[2, data.columns.get_loc("basis_change_5m")] = 3.0
        end, _ = v52.classify_outside_acceptance(data, 0, 100.0, 1)
        self.assertIsNone(end)


if __name__ == "__main__":
    unittest.main()
