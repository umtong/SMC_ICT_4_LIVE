"""Focused causal tests for candidate-04 V53."""
from __future__ import annotations

import unittest

import pandas as pd

import common_factor_deleveraging_continuation_compiler as v53


def frame(values: list[float]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(values), freq="min", tz="UTC")
    return pd.DataFrame({"metric_sum_open_interest": values}, index=index)


class V53Tests(unittest.TestCase):
    def test_contraction_threshold_is_past_only(self) -> None:
        values = pd.Series([-0.01] * 720 + [-0.50])
        threshold = v53.shifted_contraction_median(values)
        self.assertAlmostEqual(float(threshold.iloc[-1]), 0.01)

    def test_material_contraction_must_remain_at_retest(self) -> None:
        data = frame([100.0, 100.0, 96.0, 96.5])
        passed, details = v53.state_oi_contraction(
            data,
            1,
            3,
            0.03,
            maximum_rebuild_share=0.20,
        )
        self.assertTrue(passed)
        self.assertAlmostEqual(details["state_oi_contraction"], 0.04)
        self.assertAlmostEqual(details["retained_oi_contraction"], 0.035)
        self.assertLessEqual(details["oi_rebuild_share"], 0.20)

    def test_rebuilt_open_interest_invalidates_deleveraging_state(self) -> None:
        data = frame([100.0, 100.0, 96.0, 99.0])
        passed, details = v53.state_oi_contraction(
            data,
            1,
            3,
            0.03,
            maximum_rebuild_share=0.20,
        )
        self.assertFalse(passed)
        self.assertGreater(details["oi_rebuild_share"], 0.20)

    def test_ablation_removes_only_oi_mechanism(self) -> None:
        data = frame([100.0, 100.0, 101.0, 101.0])
        baseline, _ = v53.state_oi_contraction(
            data,
            1,
            3,
            0.03,
            require_contraction=True,
        )
        ablated, details = v53.state_oi_contraction(
            data,
            1,
            3,
            0.03,
            require_contraction=False,
        )
        self.assertFalse(baseline)
        self.assertTrue(ablated)
        self.assertEqual(details["oi_contraction_required"], 0.0)


if __name__ == "__main__":
    unittest.main()
