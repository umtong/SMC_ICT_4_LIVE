from __future__ import annotations

from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch

from c10_v48_overlay import aac_event_leader_only_enabled
from c10_v48_overlay import require_aac_event_direction_leader


def plan(rank: object, *, scenario: str = "AAC") -> SimpleNamespace:
    return SimpleNamespace(
        details={
            "market_leadership": {
                "symbol": "BTCUSDT",
                "scenario": scenario,
                "direction": "LONG",
                "sweep_ts_ns": 100,
                "confirmation_ts_ns": 200,
                "event_direction_rank": rank,
                "candidate_event_move": 0.01,
                "peer_event_median": 0.004,
                "peer_returns": {
                    "ETHUSDT": 0.005,
                    "SOLUSDT": 0.003,
                    "XRPUSDT": 0.002,
                },
                "leader": "BTCUSDT",
                "reason": "LEADER_AAC_EVENT_ACCEPTANCE",
            },
        },
    )


class V48AACEventLeaderTest(unittest.TestCase):
    def test_disabled_router_preserves_aac(self) -> None:
        with patch.dict(
            os.environ,
            {"C10_V48_AAC_EVENT_LEADER_ONLY": "0"},
        ):
            decision = require_aac_event_direction_leader(plan(3))
        self.assertTrue(decision.approved)
        self.assertFalse(decision.details["applied"])
        self.assertEqual(decision.event_direction_rank, 3)

    def test_rank_one_aac_is_approved(self) -> None:
        with patch.dict(
            os.environ,
            {"C10_V48_AAC_EVENT_LEADER_ONLY": "1"},
        ):
            decision = require_aac_event_direction_leader(plan(1))
        self.assertTrue(decision.approved)
        self.assertEqual(
            decision.reason,
            "AAC_CANDIDATE_IS_EVENT_DIRECTION_LEADER",
        )
        self.assertEqual(decision.details["new_fitted_thresholds"], [])

    def test_rank_two_or_worse_is_rejected(self) -> None:
        for rank in (2, 3, 4):
            with self.subTest(rank=rank), patch.dict(
                os.environ,
                {"C10_V48_AAC_EVENT_LEADER_ONLY": "1"},
            ):
                decision = require_aac_event_direction_leader(plan(rank))
                self.assertFalse(decision.approved)
                self.assertEqual(
                    decision.reason,
                    "AAC_CANDIDATE_NOT_EVENT_DIRECTION_LEADER",
                )

    def test_non_aac_fails_closed_when_router_is_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {"C10_V48_AAC_EVENT_LEADER_ONLY": "1"},
        ):
            decision = require_aac_event_direction_leader(
                plan(1, scenario="FAR"),
            )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "AAC_EVENT_ROUTER_RECEIVED_NON_AAC_PLAN",
        )

    def test_missing_rank_fails_closed(self) -> None:
        with patch.dict(
            os.environ,
            {"C10_V48_AAC_EVENT_LEADER_ONLY": "1"},
        ):
            decision = require_aac_event_direction_leader(plan(None))
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "AAC_EVENT_DIRECTION_RANK_UNAVAILABLE",
        )

    def test_environment_flag_is_exact(self) -> None:
        with patch.dict(
            os.environ,
            {"C10_V48_AAC_EVENT_LEADER_ONLY": "1"},
        ):
            self.assertTrue(aac_event_leader_only_enabled())
        with patch.dict(
            os.environ,
            {"C10_V48_AAC_EVENT_LEADER_ONLY": "0"},
        ):
            self.assertFalse(aac_event_leader_only_enabled())


if __name__ == "__main__":
    unittest.main()
