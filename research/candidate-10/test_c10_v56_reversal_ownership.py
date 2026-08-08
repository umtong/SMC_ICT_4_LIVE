from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

from c10_v56_overlay import classify_reversal_ownership


def plan(*, trend: float | None, rank: int | None, market_count: int = 4):
    if market_count < 1:
        raise ValueError("market_count must be positive")
    returns = {"BTCUSDT": 0.0}
    returns.update({f"S{i}": 0.0 for i in range(market_count - 1)})
    trends = {name: -0.1 for name in returns}
    trends["BTCUSDT"] = trend
    return SimpleNamespace(details={"market_leadership": {
        "symbol": "BTCUSDT",
        "scenario": "FAR",
        "directional_returns": returns,
        "directional_trend_scores": trends,
        "trailing_direction_rank": rank,
    }})


class ReversalOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("C10_V56_REVERSAL_OWNERSHIP")
        os.environ["C10_V56_REVERSAL_OWNERSHIP"] = "1"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("C10_V56_REVERSAL_OWNERSHIP", None)
        else:
            os.environ["C10_V56_REVERSAL_OWNERSHIP"] = self.previous

    def test_true_reversal_and_top_half_are_approved(self) -> None:
        decision = classify_reversal_ownership(
            plan(trend=-0.01, rank=2),
        )
        self.assertTrue(decision.approved)
        self.assertEqual(
            decision.reason,
            "PRE_EVENT_REVERSAL_OWNERSHIP_CONFIRMED",
        )
        self.assertEqual(decision.details["existing_top_half_limit"], 2)

    def test_same_direction_trailing_auction_is_rejected(self) -> None:
        decision = classify_reversal_ownership(
            plan(trend=0.01, rank=1),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "FAILED_AUCTION_DOES_NOT_REVERSE_CANDIDATE_TRAILING_AUCTION",
        )

    def test_trailing_laggard_is_rejected_without_new_score(self) -> None:
        decision = classify_reversal_ownership(
            plan(trend=-0.01, rank=3),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "FAILED_AUCTION_CANDIDATE_LACKS_TRAILING_REVERSAL_OWNERSHIP",
        )

    def test_missing_inputs_fail_closed(self) -> None:
        self.assertFalse(
            classify_reversal_ownership(plan(trend=None, rank=1)).approved,
        )
        self.assertFalse(
            classify_reversal_ownership(plan(trend=-0.1, rank=None)).approved,
        )

    def test_disabled_is_exact_ablation(self) -> None:
        os.environ["C10_V56_REVERSAL_OWNERSHIP"] = "0"
        decision = classify_reversal_ownership(
            plan(trend=1.0, rank=4),
        )
        self.assertTrue(decision.approved)
        self.assertFalse(decision.details["applied"])


if __name__ == "__main__":
    unittest.main()
