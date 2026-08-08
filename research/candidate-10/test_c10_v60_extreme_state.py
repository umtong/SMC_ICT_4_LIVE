from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

from c10_v60_overlay import classify_cross_sectional_extreme_state


class ExtremeStateRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old = os.environ.get("C10_V60_EXTREME_STATE_ROUTER")
        os.environ["C10_V60_EXTREME_STATE_ROUTER"] = "1"

    def tearDown(self) -> None:
        if self.old is None:
            os.environ.pop("C10_V60_EXTREME_STATE_ROUTER", None)
        else:
            os.environ["C10_V60_EXTREME_STATE_ROUTER"] = self.old

    @staticmethod
    def plan(trailing_rank: int, event_rank: int = 1) -> SimpleNamespace:
        returns = {
            "BTCUSDT": 0.04,
            "ETHUSDT": 0.03,
            "SOLUSDT": 0.02,
            "XRPUSDT": 0.01,
        }
        return SimpleNamespace(
            details={
                "market_leadership": {
                    "symbol": "BTCUSDT",
                    "scenario": "FAR",
                    "direction": "LONG",
                    "sweep_ts_ns": 1,
                    "confirmation_ts_ns": 2,
                    "trailing_direction_rank": trailing_rank,
                    "event_direction_rank": event_rank,
                    "directional_returns": returns,
                    "candidate_event_move": 0.01,
                    "peer_event_median": 0.003,
                    "event_path_efficiency": 0.2,
                    "event_standardized_displacement": 1.1,
                    "confirmation_impulse": 2.0,
                },
            },
        )

    def test_rank_one_resilience_is_approved(self) -> None:
        decision = classify_cross_sectional_extreme_state(self.plan(1))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "RELATIVE_STRENGTH_RESILIENCE")
        self.assertEqual(decision.market_count, 4)

    def test_last_to_first_rank_reversal_is_approved(self) -> None:
        decision = classify_cross_sectional_extreme_state(self.plan(4))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "CROSS_SECTIONAL_RANK_REVERSAL")

    def test_interior_rank_is_unresolved(self) -> None:
        decision = classify_cross_sectional_extreme_state(self.plan(2))
        self.assertFalse(decision.approved)
        self.assertEqual(decision.state, "UNRESOLVED")
        self.assertEqual(
            decision.reason,
            "AMBIGUOUS_INTERIOR_CROSS_SECTIONAL_STATE",
        )

    def test_event_rank_must_still_be_one(self) -> None:
        decision = classify_cross_sectional_extreme_state(
            self.plan(1, event_rank=2),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "CANDIDATE_NOT_EVENT_DIRECTION_LEADER",
        )

    def test_disabled_router_preserves_plan(self) -> None:
        os.environ["C10_V60_EXTREME_STATE_ROUTER"] = "0"
        decision = classify_cross_sectional_extreme_state(self.plan(2))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "DISABLED")
        self.assertFalse(decision.details["applied"])

    def test_terminal_rank_comes_from_market_count(self) -> None:
        plan = self.plan(3)
        del plan.details["market_leadership"]["directional_returns"]["XRPUSDT"]
        decision = classify_cross_sectional_extreme_state(plan)
        self.assertTrue(decision.approved)
        self.assertEqual(decision.market_count, 3)
        self.assertEqual(decision.state, "CROSS_SECTIONAL_RANK_REVERSAL")


if __name__ == "__main__":
    unittest.main()
