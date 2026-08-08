from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

from c10_v59_overlay import classify_true_reversal


def plan(trend: float | None):
    return SimpleNamespace(details={"market_leadership": {
        "symbol": "BTCUSDT",
        "scenario": "FAR",
        "directional_trend_scores": {"BTCUSDT": trend},
        "trailing_direction_rank": 4,
    }})


class TrueReversalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("C10_V59_TRUE_REVERSAL")
        os.environ["C10_V59_TRUE_REVERSAL"] = "1"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("C10_V59_TRUE_REVERSAL", None)
        else:
            os.environ["C10_V59_TRUE_REVERSAL"] = self.previous

    def test_negative_trailing_directional_trend_is_true_reversal(self) -> None:
        decision = classify_true_reversal(plan(-0.01))
        self.assertTrue(decision.approved)
        self.assertEqual(
            decision.reason,
            "TRUE_PRE_EVENT_REVERSAL_CONFIRMED",
        )

    def test_same_direction_extension_is_rejected(self) -> None:
        decision = classify_true_reversal(plan(0.01))
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "FAILED_AUCTION_EXTENDS_CANDIDATE_TRAILING_AUCTION",
        )

    def test_zero_is_not_a_completed_opposite_auction(self) -> None:
        self.assertFalse(classify_true_reversal(plan(0.0)).approved)

    def test_trailing_rank_is_not_used(self) -> None:
        decision = classify_true_reversal(plan(-0.01))
        self.assertTrue(decision.approved)
        self.assertIn(
            "cross-market trailing rank",
            decision.details["not_used"],
        )

    def test_missing_input_fails_closed(self) -> None:
        self.assertFalse(classify_true_reversal(plan(None)).approved)

    def test_disabled_is_exact_ablation(self) -> None:
        os.environ["C10_V59_TRUE_REVERSAL"] = "0"
        decision = classify_true_reversal(plan(1.0))
        self.assertTrue(decision.approved)
        self.assertFalse(decision.details["applied"])


if __name__ == "__main__":
    unittest.main()
