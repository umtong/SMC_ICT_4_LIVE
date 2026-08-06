from __future__ import annotations

import unittest

from nt_causal_risk_sizing import adverse_transition_gaps
from nt_causal_risk_sizing import nearest_rank_quantile


class CausalRiskSizingTests(unittest.TestCase):
    def test_nearest_rank_quantile_is_deterministic(self) -> None:
        self.assertEqual(nearest_rank_quantile([1, 2, 3, 4], 0.75), 3.0)
        self.assertEqual(nearest_rank_quantile([4, 1, 3, 2], 1.0), 4.0)

    def test_long_and_short_adverse_excursions_are_directional(self) -> None:
        rows = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
            {"open": 103.0, "high": 105.0, "low": 101.0, "close": 102.0},
            {"open": 99.0, "high": 103.0, "low": 97.0, "close": 101.0},
        ]
        self.assertEqual(adverse_transition_gaps(rows, 1), [5.0, 1.0])
        self.assertEqual(adverse_transition_gaps(rows, -1), [0.0, 5.0])

    def test_empty_quantile_is_zero(self) -> None:
        self.assertEqual(nearest_rank_quantile([], 0.95), 0.0)


if __name__ == "__main__":
    unittest.main()
