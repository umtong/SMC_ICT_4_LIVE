from __future__ import annotations

import math
import unittest

from nt_conditional_adverse_fill_risk_sizing import (
    causal_conditional_adverse_entry_deterioration,
)
from nt_expected_fill_risk_sizing import causal_expected_entry_deterioration


class ConditionalAdverseExpectationTests(unittest.TestCase):
    @staticmethod
    def rows(changes: list[float], start: float = 100.0) -> list[dict[str, float]]:
        close = start
        rows = [{"close": close}]
        for change in changes:
            close += change
            rows.append({"close": close})
        return rows

    def test_favorable_transitions_do_not_dilute_adverse_fill_expectation(self) -> None:
        changes = [2.0, -4.0, 0.0, -6.0] * 40
        rows = self.rows(changes)
        unconditional, count = causal_expected_entry_deterioration(rows, side=-1)
        conditional, conditional_count = (
            causal_conditional_adverse_entry_deterioration(rows, side=-1)
        )
        self.assertEqual(count, conditional_count)
        self.assertAlmostEqual(unconditional, 2.5)
        self.assertAlmostEqual(conditional, 5.0)
        self.assertGreater(conditional, unconditional)

    def test_current_or_future_transition_is_not_available(self) -> None:
        changes = [-2.0] * 120
        rows = self.rows(changes)
        baseline, _ = causal_conditional_adverse_entry_deterioration(rows, side=-1)
        changed = [*rows, {"close": rows[-1]["close"] - 10_000.0}]
        contaminated, _ = causal_conditional_adverse_entry_deterioration(changed, side=-1)
        self.assertTrue(math.isfinite(baseline))
        self.assertNotEqual(baseline, contaminated)
        self.assertAlmostEqual(baseline, 2.0)

    def test_insufficient_completed_history_fails_closed(self) -> None:
        value, observations = causal_conditional_adverse_entry_deterioration(
            self.rows([-1.0] * 20),
            side=-1,
        )
        self.assertTrue(math.isnan(value))
        self.assertEqual(observations, 20)


if __name__ == "__main__":
    unittest.main()
