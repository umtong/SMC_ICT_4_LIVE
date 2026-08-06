from __future__ import annotations

import math
import unittest

from nt_expected_fill_risk_sizing import (
    EXPECTED_GAP_MIN_OBSERVATIONS,
    causal_expected_entry_deterioration,
    directional_entry_excursions,
)


class ExpectedFillSizingTests(unittest.TestCase):
    def test_directional_excursions_use_opposite_price_sides(self) -> None:
        rows = [
            {"close": 100.0, "high": 100.0, "low": 100.0},
            {"close": 101.0, "high": 103.0, "low": 99.0},
            {"close": 99.0, "high": 102.0, "low": 97.0},
        ]
        self.assertEqual(
            directional_entry_excursions(rows, 1),
            [3.0, 1.0],
        )
        self.assertEqual(
            directional_entry_excursions(rows, -1),
            [1.0, 4.0],
        )

    def test_expected_value_includes_zero_excursions(self) -> None:
        rows = []
        close = 100.0
        for index in range(EXPECTED_GAP_MIN_OBSERVATIONS + 1):
            rows.append(
                {
                    "close": close,
                    "high": close + (2.0 if index % 2 else -1.0),
                    "low": close - 1.0,
                }
            )
        value, observations = causal_expected_entry_deterioration(rows, 1)
        self.assertEqual(observations, EXPECTED_GAP_MIN_OBSERVATIONS)
        self.assertTrue(math.isfinite(value))
        self.assertAlmostEqual(value, 1.0)

    def test_insufficient_completed_history_is_not_fabricated(self) -> None:
        rows = [
            {"close": 100.0, "high": 101.0, "low": 99.0}
            for _ in range(EXPECTED_GAP_MIN_OBSERVATIONS)
        ]
        value, observations = causal_expected_entry_deterioration(rows, 1)
        self.assertEqual(observations, EXPECTED_GAP_MIN_OBSERVATIONS - 1)
        self.assertTrue(math.isnan(value))

    def test_invalid_side_returns_no_excursions(self) -> None:
        rows = [{"close": 100.0, "high": 101.0, "low": 99.0}] * 3
        self.assertEqual(directional_entry_excursions(rows, 0), [])


if __name__ == "__main__":
    unittest.main()
