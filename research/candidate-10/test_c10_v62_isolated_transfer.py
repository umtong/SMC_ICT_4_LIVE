from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

from c10_v62_overlay import classify_isolated_extreme_transfer


class IsolatedExtremeTransferTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old = os.environ.get("C10_V62_ISOLATED_EXTREME_TRANSFER")
        os.environ["C10_V62_ISOLATED_EXTREME_TRANSFER"] = "1"

    def tearDown(self) -> None:
        if self.old is None:
            os.environ.pop("C10_V62_ISOLATED_EXTREME_TRANSFER", None)
        else:
            os.environ["C10_V62_ISOLATED_EXTREME_TRANSFER"] = self.old

    @staticmethod
    def plan(
        trailing_rank: int,
        peer_median: float,
        event_rank: int = 1,
        market_count: int = 4,
    ) -> SimpleNamespace:
        symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")[:market_count]
        returns = {symbol: 0.04 - i * 0.01 for i, symbol in enumerate(symbols)}
        return SimpleNamespace(
            details={
                "market_leadership": {
                    "symbol": symbols[0],
                    "scenario": "FAR",
                    "direction": "LONG",
                    "sweep_ts_ns": 1,
                    "confirmation_ts_ns": 2,
                    "trailing_direction_rank": trailing_rank,
                    "event_direction_rank": event_rank,
                    "peer_event_median": peer_median,
                    "directional_returns": returns,
                    "peer_returns": {symbol: -0.001 for symbol in symbols[1:]},
                },
            },
        )

    def test_isolated_leader_resilience(self) -> None:
        decision = classify_isolated_extreme_transfer(self.plan(1, -0.001))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "ISOLATED_LEADER_RESILIENCE")

    def test_isolated_last_to_first(self) -> None:
        decision = classify_isolated_extreme_transfer(self.plan(4, 0.0))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "ISOLATED_LAST_TO_FIRST_REVERSAL")

    def test_terminal_rank_is_peer_count_relative(self) -> None:
        decision = classify_isolated_extreme_transfer(
            self.plan(3, -0.001, market_count=3),
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "ISOLATED_LAST_TO_FIRST_REVERSAL")

    def test_positive_peer_median_is_broad_confirmation(self) -> None:
        decision = classify_isolated_extreme_transfer(self.plan(1, 0.001))
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.state,
            "BROAD_MARKET_DIRECTIONAL_CONFIRMATION",
        )

    def test_interior_rank_is_unresolved(self) -> None:
        decision = classify_isolated_extreme_transfer(self.plan(2, -0.001))
        self.assertFalse(decision.approved)
        self.assertEqual(decision.state, "UNRESOLVED")

    def test_event_rank_one_remains_required(self) -> None:
        decision = classify_isolated_extreme_transfer(
            self.plan(1, -0.001, event_rank=2),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "CANDIDATE_NOT_EVENT_DIRECTION_LEADER",
        )

    def test_disabled_is_exact_pass_through(self) -> None:
        os.environ["C10_V62_ISOLATED_EXTREME_TRANSFER"] = "0"
        decision = classify_isolated_extreme_transfer(self.plan(2, 0.001))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "DISABLED")
        self.assertFalse(decision.details["applied"])


if __name__ == "__main__":
    unittest.main()
