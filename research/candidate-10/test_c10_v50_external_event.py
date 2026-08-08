from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

from c10_v50_overlay import require_external_event_leader


class V50ExternalEventLeaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("C10_V50_EXTERNAL_EVENT_LEADER")

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("C10_V50_EXTERNAL_EVENT_LEADER", None)
        else:
            os.environ["C10_V50_EXTERNAL_EVENT_LEADER"] = self.previous

    @staticmethod
    def plan(rank: int, draw: str = "EXTERNAL_HAZARD_DOMINANCE") -> SimpleNamespace:
        return SimpleNamespace(
            scenario=SimpleNamespace(value="FAR"),
            details={
                "draw_method": draw,
                "market_leadership": {
                    "scenario": "FAR",
                    "symbol": "BTCUSDT",
                    "direction": "LONG",
                    "event_direction_rank": rank,
                    "candidate_event_move": 0.01,
                    "peer_event_median": 0.002,
                },
            },
        )

    def test_disabled_is_exact_pass_through(self) -> None:
        os.environ["C10_V50_EXTERNAL_EVENT_LEADER"] = "0"
        self.assertTrue(require_external_event_leader(self.plan(4)).approved)

    def test_rank_one_external_draw_is_approved(self) -> None:
        os.environ["C10_V50_EXTERNAL_EVENT_LEADER"] = "1"
        decision = require_external_event_leader(self.plan(1))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.event_direction_rank, 1)

    def test_rank_two_is_rejected_without_magnitude_threshold(self) -> None:
        os.environ["C10_V50_EXTERNAL_EVENT_LEADER"] = "1"
        decision = require_external_event_leader(self.plan(2))
        self.assertFalse(decision.approved)
        self.assertEqual(decision.details["new_fitted_thresholds"], [])

    def test_nonindependent_draw_fails_closed(self) -> None:
        os.environ["C10_V50_EXTERNAL_EVENT_LEADER"] = "1"
        decision = require_external_event_leader(self.plan(1, "SOURCE_RANGE_ACCEPTANCE"))
        self.assertFalse(decision.approved)


if __name__ == "__main__":
    unittest.main()
