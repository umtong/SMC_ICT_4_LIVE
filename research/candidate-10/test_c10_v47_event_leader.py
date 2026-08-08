from __future__ import annotations

from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch

from c10_v47_overlay import event_leader_only_enabled
from c10_v47_overlay import require_event_direction_leader


def plan(rank: object, *, move: float = 0.01) -> SimpleNamespace:
    return SimpleNamespace(
        details={
            "market_leadership": {
                "symbol": "BTCUSDT",
                "scenario": "FAR",
                "direction": "LONG",
                "sweep_ts_ns": 100,
                "confirmation_ts_ns": 200,
                "event_direction_rank": rank,
                "candidate_event_move": move,
                "peer_event_median": 0.003,
                "peer_returns": {
                    "ETHUSDT": 0.004,
                    "SOLUSDT": 0.002,
                    "XRPUSDT": -0.001,
                },
                "leader": "BTCUSDT",
            },
        },
    )


class V47EventLeaderTest(unittest.TestCase):
    def test_disabled_router_is_exact_pass_through(self) -> None:
        with patch.dict(os.environ, {"C10_V47_EVENT_LEADER_ONLY": "0"}):
            decision = require_event_direction_leader(plan(4))
        self.assertTrue(decision.approved)
        self.assertFalse(decision.details["applied"])
        self.assertEqual(decision.event_direction_rank, 4)

    def test_rank_one_is_approved(self) -> None:
        with patch.dict(os.environ, {"C10_V47_EVENT_LEADER_ONLY": "1"}):
            decision = require_event_direction_leader(plan(1))
        self.assertTrue(decision.approved)
        self.assertEqual(
            decision.reason,
            "CANDIDATE_IS_EVENT_DIRECTION_LEADER",
        )
        self.assertTrue(decision.details["applied"])
        self.assertEqual(decision.details["approval_contract"], (
            "EVENT_DIRECTION_RANK_EQUALS_ONE"
        ))

    def test_rank_two_or_worse_is_rejected_without_threshold(self) -> None:
        for rank in (2, 3, 4):
            with self.subTest(rank=rank), patch.dict(
                os.environ,
                {"C10_V47_EVENT_LEADER_ONLY": "1"},
            ):
                decision = require_event_direction_leader(plan(rank))
                self.assertFalse(decision.approved)
                self.assertEqual(
                    decision.reason,
                    "CANDIDATE_NOT_EVENT_DIRECTION_LEADER",
                )
                self.assertEqual(decision.event_direction_rank, rank)
                self.assertEqual(decision.details["new_fitted_thresholds"], [])

    def test_missing_rank_fails_closed(self) -> None:
        with patch.dict(os.environ, {"C10_V47_EVENT_LEADER_ONLY": "1"}):
            decision = require_event_direction_leader(plan(None))
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "EVENT_DIRECTION_RANK_UNAVAILABLE")

    def test_quote_notional_leader_identity_does_not_control_approval(self) -> None:
        candidate = plan(1)
        candidate.details["market_leadership"]["leader"] = "ETHUSDT"
        with patch.dict(os.environ, {"C10_V47_EVENT_LEADER_ONLY": "1"}):
            decision = require_event_direction_leader(candidate)
        self.assertTrue(decision.approved)
        self.assertEqual(decision.details["quote_notional_leader"], "ETHUSDT")

    def test_environment_flag_is_exact(self) -> None:
        with patch.dict(os.environ, {"C10_V47_EVENT_LEADER_ONLY": "1"}):
            self.assertTrue(event_leader_only_enabled())
        with patch.dict(os.environ, {"C10_V47_EVENT_LEADER_ONLY": "0"}):
            self.assertFalse(event_leader_only_enabled())


if __name__ == "__main__":
    unittest.main()
