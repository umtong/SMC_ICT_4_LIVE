from __future__ import annotations

import math
import unittest

from nt_expected_fill_risk_sizing import (
    EXPECTED_GAP_MIN_OBSERVATIONS,
    FILL_EXPECTATION_CONTRACT,
    causal_expected_entry_deterioration,
    directional_entry_excursions,
)


class ExpectedFillSizingTests(unittest.TestCase):
    def test_directional_transitions_use_completed_closes(self) -> None:
        rows = [
            {"close": 100.0, "high": 150.0, "low": 50.0},
            {"close": 101.0, "high": 160.0, "low": 40.0},
            {"close": 99.0, "high": 170.0, "low": 30.0},
        ]
        self.assertEqual(
            directional_entry_excursions(rows, 1),
            [1.0, 0.0],
        )
        self.assertEqual(
            directional_entry_excursions(rows, -1),
            [0.0, 2.0],
        )

    def test_expected_value_includes_zero_and_favorable_transitions(self) -> None:
        rows = []
        close = 100.0
        rows.append({"close": close, "high": close, "low": close})
        for index in range(EXPECTED_GAP_MIN_OBSERVATIONS):
            close += 2.0 if index % 2 == 0 else -2.0
            rows.append({"close": close, "high": close, "low": close})
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

    def test_contract_name_records_engine_aligned_relation(self) -> None:
        self.assertEqual(
            FILL_EXPECTATION_CONTRACT,
            "mean_positive_completed_directional_close_transition_plus_one_tick",
        )


if __name__ == "__main__":
    unittest.main()
