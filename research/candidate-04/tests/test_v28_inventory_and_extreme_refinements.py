from __future__ import annotations

import math
import unittest

import pandas as pd

import balanced_session_material_inventory_compiler as material
import failed_external_break_retest_extreme_tail_compiler as extreme


class ExtremeParentBreakTests(unittest.TestCase):
    def test_extreme_cutoff_uses_only_prior_rows(self) -> None:
        class Config:
            stress_inventory_quantile_window_minutes = 4
            stress_inventory_quantile_min_periods = 4

        data = pd.DataFrame({"ret_60s_bps": [1.0, 2.0, 3.0, 4.0, 100.0]})
        cutoff = extreme.past_only_extreme_impact_cutoff(data, Config())
        self.assertTrue(math.isnan(float(cutoff.iloc[3])))
        self.assertAlmostEqual(float(cutoff.iloc[4]), 3.97)

    def test_parent_break_must_reach_extreme_cutoff(self) -> None:
        self.assertTrue(extreme.is_extreme_parent_impact(20.0, 20.0))
        self.assertFalse(extreme.is_extreme_parent_impact(19.99, 20.0))
        self.assertFalse(extreme.is_extreme_parent_impact(20.0, float("nan")))


class MaterialInventoryTests(unittest.TestCase):
    def test_material_cutoff_is_median_of_prior_positive_steps(self) -> None:
        class Config:
            stress_inventory_quantile_window_minutes = 64

        # 31 positive steps provide the required 30 prior observations at the
        # final row. The current large step must not calibrate its own cutoff.
        levels = [100.0]
        for step in range(1, 33):
            levels.append(levels[-1] * (1.0 + step / 100_000.0))
        data = pd.DataFrame({"metric_sum_open_interest": levels})
        cutoff = material.past_only_material_positive_oi_cutoff(data, Config())
        prior_changes = pd.Series(levels).pct_change().iloc[1:-1]
        expected = float(prior_changes.quantile(0.50))
        self.assertAlmostEqual(float(cutoff.iloc[-1]), expected)

    def test_material_inventory_rejects_microscopic_change(self) -> None:
        self.assertTrue(
            material.is_material_inventory_expansion(0.0004, 0.0003)
        )
        self.assertFalse(
            material.is_material_inventory_expansion(0.00001, 0.0003)
        )
        self.assertFalse(
            material.is_material_inventory_expansion(0.0004, float("nan"))
        )


if __name__ == "__main__":
    unittest.main()
