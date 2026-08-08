from __future__ import annotations

import unittest

from week_long_failure_audit import compound
from week_long_failure_audit import lag_one_autocorrelation
from week_long_failure_audit import longest_loss_streak
from week_long_failure_audit import rolling_compounds


class WeekLongFailureAuditTests(unittest.TestCase):
    def test_compound_preserves_geometric_path(self) -> None:
        self.assertAlmostEqual(compound([0.10, -0.10]), -0.01)
        self.assertEqual(compound([-1.0]), -1.0)

    def test_rolling_windows_require_contiguous_dates(self) -> None:
        result = rolling_compounds(
            {
                "2024-03-01": 0.01,
                "2024-03-02": 0.02,
                "2024-03-03": -0.01,
                "2024-03-05": 0.04,
            },
            3,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start"], "2024-03-01")
        self.assertEqual(result[0]["end"], "2024-03-03")
        self.assertAlmostEqual(
            result[0]["total_return"],
            (1.01 * 1.02 * 0.99) - 1.0,
        )

    def test_dependence_diagnostics_do_not_change_returns(self) -> None:
        values = [-1.0, -0.5, 0.4, -0.1, -0.2, -0.3, 0.2]
        self.assertEqual(longest_loss_streak(values), 3)
        correlation = lag_one_autocorrelation(values)
        self.assertIsInstance(correlation, float)
        self.assertGreaterEqual(correlation, -1.0)
        self.assertLessEqual(correlation, 1.0)


if __name__ == "__main__":
    unittest.main()
