from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

from c10_v61_overlay import classify_peer_nonconfirmation


class PeerNonconfirmationRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old = os.environ.get("C10_V61_PEER_NONCONFIRMATION_ROUTER")
        os.environ["C10_V61_PEER_NONCONFIRMATION_ROUTER"] = "1"

    def tearDown(self) -> None:
        if self.old is None:
            os.environ.pop("C10_V61_PEER_NONCONFIRMATION_ROUTER", None)
        else:
            os.environ["C10_V61_PEER_NONCONFIRMATION_ROUTER"] = self.old

    @staticmethod
    def plan(peer_median: float, rank: int = 1) -> SimpleNamespace:
        return SimpleNamespace(
            details={
                "market_leadership": {
                    "symbol": "BTCUSDT",
                    "scenario": "FAR",
                    "direction": "LONG",
                    "sweep_ts_ns": 1,
                    "confirmation_ts_ns": 2,
                    "event_direction_rank": rank,
                    "peer_event_median": peer_median,
                    "candidate_event_move": -0.001,
                    "peer_returns": {
                        "ETHUSDT": -0.002,
                        "SOLUSDT": -0.003,
                        "XRPUSDT": 0.0001,
                    },
                },
            },
        )

    def test_negative_peer_median_is_nonconfirmation(self) -> None:
        decision = classify_peer_nonconfirmation(self.plan(-0.001))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "SMT_PEER_NONCONFIRMATION")

    def test_zero_is_structural_nonconfirmation_boundary(self) -> None:
        decision = classify_peer_nonconfirmation(self.plan(0.0))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.details["economic_boundary"], 0.0)

    def test_positive_peer_median_is_broad_confirmation(self) -> None:
        decision = classify_peer_nonconfirmation(self.plan(0.001))
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.state,
            "BROAD_MARKET_DIRECTIONAL_CONFIRMATION",
        )

    def test_event_rank_one_remains_required(self) -> None:
        decision = classify_peer_nonconfirmation(self.plan(-0.001, rank=2))
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "CANDIDATE_NOT_EVENT_DIRECTION_LEADER",
        )

    def test_missing_input_fails_closed(self) -> None:
        plan = self.plan(-0.001)
        plan.details["market_leadership"]["peer_event_median"] = None
        decision = classify_peer_nonconfirmation(plan)
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "PEER_NONCONFIRMATION_INPUT_UNAVAILABLE",
        )

    def test_disabled_router_is_exact_pass_through(self) -> None:
        os.environ["C10_V61_PEER_NONCONFIRMATION_ROUTER"] = "0"
        decision = classify_peer_nonconfirmation(self.plan(0.01))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "DISABLED")
        self.assertFalse(decision.details["applied"])


if __name__ == "__main__":
    unittest.main()
