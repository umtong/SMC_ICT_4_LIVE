from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

from c10_v51_overlay import classify_transfer_role


class V51TransferRoleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("C10_V51_TRANSFER_ROLE_MODE")
        os.environ["C10_V51_TRANSFER_ROLE_MODE"] = "CAUSAL_ROLE_ROUTER"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("C10_V51_TRANSFER_ROLE_MODE", None)
        else:
            os.environ["C10_V51_TRANSFER_ROLE_MODE"] = self.previous

    @staticmethod
    def plan(*, rank: int, peer_median: float, impulse: float, trend: float,
             peers: dict[str, float], symbol: str = "BTCUSDT",
             leader: str = "ETHUSDT", move: float = 0.01):
        return SimpleNamespace(
            scenario=SimpleNamespace(value="FAR"),
            details={"market_leadership": {
                "symbol": symbol, "scenario": "FAR", "direction": "LONG",
                "event_direction_rank": rank,
                "peer_event_median": peer_median,
                "candidate_event_move": move,
                "confirmation_impulse": impulse,
                "directional_trend_scores": {symbol: trend},
                "peer_returns": peers,
                "leader": leader,
            }},
        )

    def test_pioneer_must_reverse_trailing_state(self) -> None:
        good = classify_transfer_role(
            self.plan(rank=1, peer_median=-0.001, impulse=0.5, trend=-0.2,
                      peers={"ETHUSDT": -0.001}),
            minimum_confirmation_impulse=1.0,
        )
        bad = classify_transfer_role(
            self.plan(rank=1, peer_median=-0.001, impulse=2.0, trend=0.2,
                      peers={"ETHUSDT": -0.001}),
            minimum_confirmation_impulse=1.0,
        )
        self.assertTrue(good.approved)
        self.assertFalse(bad.approved)

    def test_distributed_leader_uses_frozen_impulse(self) -> None:
        decision = classify_transfer_role(
            self.plan(rank=1, peer_median=0.001, impulse=1.0, trend=0.2,
                      peers={"ETHUSDT": 0.001}),
            minimum_confirmation_impulse=1.0,
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.role, "DISTRIBUTED_LEADER")

    def test_follower_catchup_requires_unanimous_peers_and_local_impulse(self) -> None:
        decision = classify_transfer_role(
            self.plan(rank=2, peer_median=0.002, impulse=1.2, trend=-0.1,
                      peers={"ETHUSDT": 0.003, "SOLUSDT": 0.001}),
            minimum_confirmation_impulse=1.0,
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.role, "FOLLOWER_CATCHUP")

    def test_no_new_fitted_thresholds(self) -> None:
        decision = classify_transfer_role(
            self.plan(rank=3, peer_median=-0.001, impulse=2.0, trend=0.2,
                      peers={"ETHUSDT": -0.001}),
            minimum_confirmation_impulse=1.0,
        )
        self.assertEqual(decision.details["new_fitted_thresholds"], [])


if __name__ == "__main__":
    unittest.main()
