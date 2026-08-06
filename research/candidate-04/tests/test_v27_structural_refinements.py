from __future__ import annotations

import math
import unittest

import pandas as pd

import directional_session_vwap_upper_tail_compiler as directional
import failed_external_break_retest_impact_tail_compiler as impact


class ParentImpactTailTests(unittest.TestCase):
    def test_cutoff_is_shifted_before_current_observation(self) -> None:
        class Config:
            stress_inventory_quantile_window_minutes = 4
            stress_inventory_quantile_min_periods = 4
            stress_inventory_quantile = 0.5

        data = pd.DataFrame({"ret_60s_bps": [1.0, 2.0, 3.0, 4.0, 100.0]})
        cutoff = impact.past_only_impact_cutoff(data, Config())
        self.assertTrue(math.isnan(float(cutoff.iloc[3])))
        self.assertAlmostEqual(float(cutoff.iloc[4]), 2.5)

    def test_impact_tail_requires_finite_threshold(self) -> None:
        self.assertTrue(impact.is_parent_impact_tail(12.0, 10.0))
        self.assertFalse(impact.is_parent_impact_tail(9.0, 10.0))
        self.assertFalse(impact.is_parent_impact_tail(12.0, float("nan")))


class DirectionalSessionUpperTailTests(unittest.TestCase):
    def test_upper_quartile_is_past_only_history_statistic(self) -> None:
        history = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06]
        cutoff = directional.past_efficiency_cutoff(history)
        self.assertAlmostEqual(cutoff, 0.0475)

    def test_insufficient_history_is_not_directional(self) -> None:
        cutoff = directional.past_efficiency_cutoff([0.01, 0.02, 0.03])
        self.assertTrue(math.isnan(cutoff))


if __name__ == "__main__":
    unittest.main()
