from __future__ import annotations

import math
import unittest

from nt_conditional_adverse_fill_risk_sizing import (
    causal_conditional_adverse_entry_deterioration,
)
from nt_tail_adverse_fill_risk_sizing import (
    ADVERSE_ENTRY_QUANTILE,
    causal_tail_adverse_entry_deterioration,
)


class TailAdverseExpectationTests(unittest.TestCase):
    @staticmethod
    def rows(changes: list[float], start: float = 1000.0) -> list[dict[str, float]]:
        close = start
        rows = [{"close": close}]
        for change in changes:
            close += change
            rows.append({"close": close})
        return rows

    def test_q95_reserves_more_than_conditional_mean_for_adverse_tail(self) -> None:
        adverse_changes = [-2.0] * 95 + [-20.0] * 5
        rows = self.rows(adverse_changes)
        conditional, count = causal_conditional_adverse_entry_deterioration(
            rows,
            side=-1,
        )
        tail, tail_count = causal_tail_adverse_entry_deterioration(rows, side=-1)
        self.assertEqual(count, tail_count)
        self.assertTrue(math.isfinite(tail))
        self.assertGreater(tail, conditional)
        self.assertEqual(ADVERSE_ENTRY_QUANTILE, 0.95)

    def test_favorable_transitions_do_not_enter_adverse_quantile(self) -> None:
        rows = self.rows(([-2.0] * 80) + ([10.0] * 80))
        value, observations = causal_tail_adverse_entry_deterioration(
            rows,
            side=-1,
        )
        self.assertEqual(observations, 160)
        self.assertAlmostEqual(value, 2.0)

    def test_insufficient_adverse_tail_history_fails_closed(self) -> None:
        rows = self.rows(([-2.0] * 20) + ([2.0] * 100))
        value, observations = causal_tail_adverse_entry_deterioration(
            rows,
            side=-1,
        )
        self.assertTrue(math.isnan(value))
        self.assertEqual(observations, 120)


if __name__ == "__main__":
    unittest.main()
