from __future__ import annotations

from dataclasses import replace
import unittest

from market_leadership import LeadershipDecision
from semantic_market_leadership import semantic_decision


def decision() -> LeadershipDecision:
    return LeadershipDecision(
        approved=False,
        reason="RAW",
        leader="BTCUSDT",
        symbol="SOLUSDT",
        scenario="FAR",
        direction="LONG",
        sweep_ts_ns=1,
        confirmation_ts_ns=2,
        peer_returns={
            "BTCUSDT": 0.002,
            "ETHUSDT": 0.003,
            "XRPUSDT": 0.001,
        },
        directional_returns={
            "BTCUSDT": 0.02,
            "ETHUSDT": 0.03,
            "SOLUSDT": 0.025,
            "XRPUSDT": 0.01,
        },
        directional_trend_scores={
            "BTCUSDT": 0.9,
            "ETHUSDT": 0.8,
            "SOLUSDT": 1.1,
            "XRPUSDT": 0.7,
        },
        candidate_event_move=0.004,
        peer_event_median=0.002,
        confirmation_impulse=1.5,
        trailing_direction_rank=1,
        event_direction_rank=2,
        event_path_efficiency=0.2,
        event_standardized_displacement=0.8,
    )


def decide(raw: LeadershipDecision) -> LeadershipDecision:
    return semantic_decision(
        raw,
        symbol_count=4,
        severe_adverse_trend_score=-1.5,
        minimum_confirmation_impulse=1.0,
        minimum_event_efficiency=0.1,
        minimum_event_displacement=0.5,
    )


class TrendResumptionFarTests(unittest.TestCase):
    def test_aligned_synchronized_top_half_far_is_approved(self) -> None:
        result = decide(decision())
        self.assertTrue(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_TREND_RESUMPTION_SYNCHRONIZED",
        )

    def test_quorum_without_unanimity_is_not_trend_resumption(self) -> None:
        raw = decision()
        raw = replace(
            raw,
            peer_returns={
                "BTCUSDT": 0.003,
                "ETHUSDT": 0.002,
                "XRPUSDT": -0.0005,
            },
        )
        result = decide(raw)
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_TREND_RESUMPTION_REQUIRES_UNANIMOUS_PEERS",
        )

    def test_mixed_trailing_auction_remains_rejected(self) -> None:
        raw = decision()
        raw = replace(
            raw,
            directional_trend_scores={
                "BTCUSDT": -0.8,
                "ETHUSDT": -0.5,
                "SOLUSDT": 0.9,
                "XRPUSDT": -0.6,
            },
        )
        result = decide(raw)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_MIXED_TRAILING_AUCTION")


if __name__ == "__main__":
    unittest.main(verbosity=2)
