from __future__ import annotations

from dataclasses import replace
import unittest

from market_leadership import LeadershipDecision
from semantic_market_leadership import (
    FAR_EXHAUSTION_UNANIMOUS,
    FAR_ROTATION_DISPLACEMENT,
)
from semantic_market_leadership_v16 import (
    FAR_ROTATION_SOURCE_NOT_TRANSFER,
    refine_v15_decision,
)


class V16RotationTransferTests(unittest.TestCase):
    @staticmethod
    def decision(
        *,
        rank: int = 1,
        approved: bool = True,
        reason: str = FAR_ROTATION_DISPLACEMENT,
    ) -> LeadershipDecision:
        return LeadershipDecision(
            approved=approved,
            reason=reason,
            leader="BTCUSDT",
            symbol="ETHUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=1,
            confirmation_ts_ns=2,
            peer_returns={"BTCUSDT": 0.004, "SOLUSDT": 0.003, "XRPUSDT": 0.002},
            directional_returns={
                "BTCUSDT": 0.01,
                "ETHUSDT": 0.01,
                "SOLUSDT": 0.01,
                "XRPUSDT": 0.01,
            },
            directional_trend_scores={
                "BTCUSDT": -0.2,
                "ETHUSDT": -0.1,
                "SOLUSDT": 0.2,
                "XRPUSDT": 0.1,
            },
            candidate_event_move=0.01,
            peer_event_median=0.003,
            confirmation_impulse=0.7,
            trailing_direction_rank=3,
            event_direction_rank=rank,
            event_path_efficiency=0.22,
            event_standardized_displacement=1.27,
        )

    def test_rank_one_event_source_is_not_rotation_transfer(self) -> None:
        result = refine_v15_decision(self.decision(rank=1))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, FAR_ROTATION_SOURCE_NOT_TRANSFER)

    def test_later_event_recipient_retains_rotation_transfer(self) -> None:
        original = self.decision(rank=3)
        self.assertIs(refine_v15_decision(original), original)

    def test_unrelated_far_role_is_unchanged(self) -> None:
        original = self.decision(rank=1, reason=FAR_EXHAUSTION_UNANIMOUS)
        self.assertIs(refine_v15_decision(original), original)

    def test_already_rejected_decision_is_unchanged(self) -> None:
        original = self.decision(rank=1, approved=False)
        self.assertIs(refine_v15_decision(original), original)

    def test_rank_metadata_is_not_rewritten(self) -> None:
        original = self.decision(rank=2)
        result = refine_v15_decision(original)
        self.assertEqual(result.event_direction_rank, 2)
        self.assertEqual(result, original)


if __name__ == "__main__":
    unittest.main()
