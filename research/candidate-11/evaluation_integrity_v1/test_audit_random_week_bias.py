from __future__ import annotations

import json
from pathlib import Path
import unittest

from audit_random_week_bias import (
    audit,
    exact_two_sided_lower_bound,
    required_trade_density_from_realized_path,
)


ROOT = Path(__file__).resolve().parent


class EvaluationIntegrityTests(unittest.TestCase):
    def snapshot(self):
        return json.loads(
            (ROOT / "evidence_snapshot.json").read_text(encoding="utf-8")
        )

    def test_exact_lower_bounds_match_known_clopper_pearson_values(self):
        self.assertAlmostEqual(
            exact_two_sided_lower_bound(7, 7), 0.590383602775, places=10
        )
        self.assertAlmostEqual(
            exact_two_sided_lower_bound(9, 11), 0.482244147640, places=10
        )
        self.assertAlmostEqual(
            exact_two_sided_lower_bound(10, 14), 0.418964742816, places=10
        )

    def test_archive_disproves_every_random_week_was_good(self):
        result = audit(self.snapshot())
        self.assertFalse(result["all_random_weeks_were_good"])
        self.assertGreaterEqual(result["archived_short_week_failure_count"], 4)
        self.assertIn(
            "SURVIVORSHIP_AND_RESEARCH_MEMORY_BIAS",
            result["failure_modes"],
        )

    def test_same_opened_weeks_cannot_be_holdout_after_source_revision(self):
        result = audit(self.snapshot())
        self.assertTrue(result["adaptive_reuse"])
        self.assertFalse(result["random_week_success_is_valid_holdout_evidence"])
        self.assertIn(
            "ADAPTIVE_REUSE_OF_OPENED_RANDOM_WEEKS",
            result["failure_modes"],
        )

    def test_combined_evidence_remains_below_project_target(self):
        result = audit(self.snapshot())
        growth = result["growth_diagnostics"]
        self.assertLess(
            growth["combined_context_daily_geometric_growth"],
            growth["project_target_daily_geometric_growth"],
        )
        self.assertIn(
            "COMBINED_EVIDENCE_BELOW_PROJECT_GROWTH_TARGET",
            result["failure_modes"],
        )

    def test_directional_domain_shift_is_detected(self):
        result = audit(self.snapshot())
        direction = result["direction_diagnostics"]
        self.assertGreaterEqual(direction["development_short_share"], 0.90)
        self.assertLessEqual(
            direction["untouched_holdout_short_share"], 1.0 / 3.0
        )
        self.assertGreater(direction["absolute_short_share_shift"], 0.50)

    def test_trade_density_required_for_target_exceeds_observed(self):
        result = audit(self.snapshot())
        density = result["opportunity_density"]
        self.assertGreater(
            density[
                "required_combined_trades_per_day_if_realized_average_log_trade_persisted"
            ],
            density["combined_trades_per_day"],
        )
        self.assertGreater(
            density["required_minus_observed_combined_trades_per_day"], 0.0
        )

    def test_nonpositive_path_has_no_feasible_required_density(self):
        self.assertIsNone(
            required_trade_density_from_realized_path(
                nav_multiple=0.98,
                trades=3,
                target_daily_growth=0.01,
            )
        )


if __name__ == "__main__":
    unittest.main()
