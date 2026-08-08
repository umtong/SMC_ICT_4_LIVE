from __future__ import annotations

from dataclasses import dataclass, field
import os
import unittest

from c10_v50_overlay import require_independent_external_far


@dataclass
class Scenario:
    value: str


@dataclass
class Plan:
    scenario: Scenario
    details: dict = field(default_factory=dict)


def plan(*, scenario: str = "FAR", rank: int | None = 1, draw: str = "EXTERNAL_HAZARD_DOMINANCE") -> Plan:
    return Plan(
        Scenario(scenario),
        {
            "draw_method": draw,
            "market_leadership": {
                "event_direction_rank": rank,
                "candidate_event_move": 0.01,
                "peer_event_median": 0.005,
                "confirmation_impulse": 1.2,
                "sweep_ts_ns": 1,
                "confirmation_ts_ns": 2,
            },
        },
    )


class V50IndependentExternalTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("C10_V50_EVENT_LEADER_ONLY", None)

    def test_baseline_preserves_certified_far(self) -> None:
        os.environ["C10_V50_EVENT_LEADER_ONLY"] = "0"
        decision = require_independent_external_far(plan(rank=4))
        self.assertTrue(decision.approved)

    def test_rank_one_is_approved(self) -> None:
        os.environ["C10_V50_EVENT_LEADER_ONLY"] = "1"
        decision = require_independent_external_far(plan(rank=1))
        self.assertTrue(decision.approved)

    def test_rank_two_is_rejected(self) -> None:
        os.environ["C10_V50_EVENT_LEADER_ONLY"] = "1"
        decision = require_independent_external_far(plan(rank=2))
        self.assertFalse(decision.approved)

    def test_non_far_is_rejected_in_both_cells(self) -> None:
        for enabled in ("0", "1"):
            os.environ["C10_V50_EVENT_LEADER_ONLY"] = enabled
            self.assertFalse(require_independent_external_far(plan(scenario="AAC")).approved)

    def test_non_independent_draw_fails_closed(self) -> None:
        os.environ["C10_V50_EVENT_LEADER_ONLY"] = "0"
        self.assertFalse(
            require_independent_external_far(
                plan(draw="SOURCE_RANGE_ACCEPTANCE"),
            ).approved,
        )

    def test_missing_rank_fails_closed_only_in_event_leader_cell(self) -> None:
        os.environ["C10_V50_EVENT_LEADER_ONLY"] = "1"
        self.assertFalse(require_independent_external_far(plan(rank=None)).approved)


if __name__ == "__main__":
    unittest.main()
