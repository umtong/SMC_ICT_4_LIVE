"""Focused causal tests for candidate-04 V54."""
from __future__ import annotations

import unittest

import pandas as pd

import common_factor_accepted_auction_failure_reversal_compiler as v54


def frame(rows: list[dict[str, float]]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(rows), freq="min", tz="UTC")
    return pd.DataFrame(rows, index=index)


class V54Tests(unittest.TestCase):
    def test_failure_requires_old_boundary_loss_mss_and_common_turn(self) -> None:
        data = frame(
            [
                {"open": 101.0, "close": 101.0, "high": 101.2, "low": 100.8, "atr": 1.0, "ret_60s_bps": 0.0, "flow_60s": 0.0, "basis_change_5m": 0.0},
                {"open": 101.0, "close": 101.1, "high": 101.2, "low": 100.9, "atr": 1.0, "ret_60s_bps": 1.0, "flow_60s": 0.1, "basis_change_5m": 0.0},
                {"open": 101.0, "close": 99.0, "high": 101.1, "low": 98.9, "atr": 1.0, "ret_60s_bps": -20.0, "flow_60s": -0.8, "basis_change_5m": -2.0},
            ]
        )
        factors = {
            "common_return": pd.Series([0.0, 0.0, -0.7], index=data.index),
            "common_flow": pd.Series([0.0, 0.0, -0.5], index=data.index),
        }
        passed, details = v54.opposite_failure_confirmation(
            data,
            factors,
            2,
            1,
            100.0,
            99.5,
            0.2,
        )
        self.assertTrue(passed)
        self.assertEqual(details["old_external_boundary_failed"], 1.0)
        factors["common_return"].iloc[2] = 0.5
        passed, _ = v54.opposite_failure_confirmation(
            data,
            factors,
            2,
            1,
            100.0,
            99.5,
            0.2,
        )
        self.assertFalse(passed)

    def test_ablation_removes_only_opposite_common_factor(self) -> None:
        data = frame(
            [
                {"open": 101.0, "close": 101.0, "high": 101.2, "low": 100.8, "atr": 1.0, "ret_60s_bps": 0.0, "flow_60s": 0.0, "basis_change_5m": 0.0},
                {"open": 101.0, "close": 101.1, "high": 101.2, "low": 100.9, "atr": 1.0, "ret_60s_bps": 1.0, "flow_60s": 0.1, "basis_change_5m": 0.0},
                {"open": 101.0, "close": 99.0, "high": 101.1, "low": 98.9, "atr": 1.0, "ret_60s_bps": -20.0, "flow_60s": -0.8, "basis_change_5m": -2.0},
            ]
        )
        factors = {
            "common_return": pd.Series([0.0, 0.0, 0.4], index=data.index),
            "common_flow": pd.Series([0.0, 0.0, 0.4], index=data.index),
        }
        baseline, _ = v54.opposite_failure_confirmation(
            data, factors, 2, 1, 100.0, 99.5, 0.2,
            require_opposite_common_factor=True,
        )
        ablated, details = v54.opposite_failure_confirmation(
            data, factors, 2, 1, 100.0, 99.5, 0.2,
            require_opposite_common_factor=False,
        )
        self.assertFalse(baseline)
        self.assertTrue(ablated)
        self.assertEqual(details["opposite_common_factor_required"], 0.0)

    def test_inside_retest_is_separate_and_holds_inverse_fvg(self) -> None:
        data = frame(
            [
                {"open": 99.5, "close": 99.4, "high": 99.8, "low": 99.2, "flow_60s": -0.2},
                {"open": 99.6, "close": 99.3, "high": 100.0, "low": 99.1, "flow_60s": -0.3},
            ]
        )
        factors = {
            "common_return": pd.Series([-0.2, -0.3], index=data.index),
            "common_flow": pd.Series([-0.1, -0.2], index=data.index),
        }
        passed, _ = v54.inside_retest_holds(
            data,
            factors,
            1,
            -1,
            100.0,
            (99.5, 99.9),
        )
        self.assertTrue(passed)


if __name__ == "__main__":
    unittest.main()
