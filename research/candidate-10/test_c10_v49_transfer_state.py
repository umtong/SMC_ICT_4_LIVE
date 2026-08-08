from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

from c10_v49_overlay import classify_transfer_state


def make_plan(peer: float | None, impulse: float | None, trend: float | None, rank: int | None = 1):
    return SimpleNamespace(details={"market_leadership": {
        "symbol": "BTCUSDT",
        "scenario": "FAR",
        "direction": "LONG",
        "event_direction_rank": rank,
        "candidate_event_move": 0.01,
        "peer_event_median": peer,
        "confirmation_impulse": impulse,
        "directional_trend_scores": {"BTCUSDT": trend},
    }})


class TransferStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("C10_V49_TRANSFER_STATE_ROUTER")
        os.environ["C10_V49_TRANSFER_STATE_ROUTER"] = "1"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("C10_V49_TRANSFER_STATE_ROUTER", None)
        else:
            os.environ["C10_V49_TRANSFER_STATE_ROUTER"] = self.previous

    def decide(self, value, threshold: float = 1.0):
        return classify_transfer_state(
            value,
            minimum_confirmation_impulse=threshold,
        )

    def test_distributed_transfer(self) -> None:
        accepted = self.decide(make_plan(0.002, 1.2, -0.5))
        rejected = self.decide(make_plan(0.002, 0.8, -0.5))
        self.assertTrue(accepted.approved)
        self.assertEqual(accepted.state, "DISTRIBUTED_TRANSFER")
        self.assertFalse(rejected.approved)

    def test_pioneer_transfer(self) -> None:
        accepted = self.decide(make_plan(-0.002, 0.2, -0.1))
        rejected = self.decide(make_plan(-0.002, 3.0, 0.1))
        self.assertTrue(accepted.approved)
        self.assertEqual(accepted.state, "PIONEER_TRANSFER")
        self.assertFalse(rejected.approved)

    def test_threshold_is_supplied(self) -> None:
        rejected = self.decide(make_plan(0.002, 1.2, -0.5), 1.5)
        self.assertFalse(rejected.approved)
        self.assertEqual(rejected.details["minimum_confirmation_impulse"], 1.5)

    def test_rank_and_inputs_are_required(self) -> None:
        self.assertFalse(self.decide(make_plan(0.1, 2.0, -1.0, 2)).approved)
        self.assertFalse(self.decide(make_plan(None, 2.0, -1.0)).approved)
        self.assertFalse(self.decide(make_plan(0.1, 2.0, None)).approved)

    def test_disabled_ablation(self) -> None:
        os.environ["C10_V49_TRANSFER_STATE_ROUTER"] = "0"
        decision = self.decide(make_plan(None, None, None))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "UNROUTED")


if __name__ == "__main__":
    unittest.main()
