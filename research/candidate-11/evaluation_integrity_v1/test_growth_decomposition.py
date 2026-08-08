from __future__ import annotations

import json
from pathlib import Path
import unittest

from decompose_growth import decompose


ROOT = Path(__file__).resolve().parent


class GrowthDecompositionTests(unittest.TestCase):
    @classmethod
    def result(cls):
        snapshot = json.loads(
            (ROOT / "evidence_snapshot.json").read_text(encoding="utf-8")
        )
        return decompose(snapshot)

    def test_log_identity_reconstructs_each_account_path(self):
        for name in ("development", "untouched_holdout", "combined_context"):
            component = self.result()[name]
            self.assertAlmostEqual(
                component["identity_reconstruction"],
                component["log_growth_per_day"],
                places=14,
            )

    def test_holdout_frequency_fell_below_half(self):
        result = self.result()
        self.assertLess(
            result["development_to_holdout"]["event_rate_ratio"], 0.5
        )

    def test_per_trade_quality_reversed_sign(self):
        result = self.result()
        self.assertGreater(
            result["development"]["average_log_growth_per_trade"], 0.0
        )
        self.assertLess(
            result["untouched_holdout"]["average_log_growth_per_trade"], 0.0
        )

    def test_shapley_contributions_sum_to_observed_gap(self):
        change = self.result()["development_to_holdout"]
        self.assertAlmostEqual(
            change["shapley_sum"],
            change["calendar_log_growth_gap_per_day"],
            places=14,
        )

    def test_quality_was_larger_gap_component(self):
        change = self.result()["development_to_holdout"]
        self.assertGreater(
            change["absolute_gap_share_quality"],
            change["absolute_gap_share_frequency"],
        )
        self.assertGreater(change["absolute_gap_share_quality"], 0.75)


if __name__ == "__main__":
    unittest.main()
