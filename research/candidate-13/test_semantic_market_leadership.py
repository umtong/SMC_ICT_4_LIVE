from __future__ import annotations

import unittest

from market_leadership import LeadershipDecision
from semantic_market_leadership import semantic_decision


class SemanticLeadershipTests(unittest.TestCase):
    def decision(self, **updates):
        base = dict(
            approved=False,
            reason="BASE_POLICY",
            leader="BTCUSDT",
            symbol="SOLUSDT",
            scenario="FAR",
            direction="SHORT",
            sweep_ts_ns=1,
            confirmation_ts_ns=2,
            peer_returns={"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": -0.002},
            directional_returns={
                "BTCUSDT": -0.01,
                "ETHUSDT": -0.01,
                "SOLUSDT": -0.02,
                "XRPUSDT": -0.01,
            },
            directional_trend_scores={
                "BTCUSDT": -1.19,
                "ETHUSDT": -0.15,
                "SOLUSDT": -1.32,
                "XRPUSDT": -0.11,
            },
            candidate_event_move=0.006,
            peer_event_median=0.003,
            confirmation_impulse=2.10,
            trailing_direction_rank=4,
            event_direction_rank=2,
            event_path_efficiency=0.12,
            event_standardized_displacement=0.86,
        )
        base.update(updates)
        return LeadershipDecision(**base)

    def classify(self, decision):
        return semantic_decision(
            decision,
            symbol_count=4,
            severe_adverse_trend_score=-1.5,
            minimum_confirmation_impulse=1.0,
            minimum_event_efficiency=0.10,
            minimum_event_displacement=0.50,
        )

    def test_far_is_moderate_countertrend_unanimous_reversal(self):
        result = self.classify(self.decision())
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS")

    def test_far_rejects_trend_following_state(self):
        scores = {symbol: value + 2.0 for symbol, value in self.decision().directional_trend_scores.items()}
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_NOT_COUNTERTREND")

    def test_far_rejects_severely_unresolved_countertrend(self):
        scores = {symbol: -2.0 for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")}
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")

    def test_far_rejects_mixed_peer_path(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": 0.001, "XRPUSDT": -0.002}
        result = self.classify(self.decision(peer_returns=peers))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_REQUIRES_UNANIMOUS_PEER_RECLAIM")

    def test_aac_accepts_own_efficient_nonlaggard_move(self):
        result = self.classify(self.decision(scenario="AAC", event_direction_rank=3))
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_SYNCHRONIZED_NONLAGGARD")

    def test_aac_rejects_last_mover(self):
        result = self.classify(self.decision(scenario="AAC", event_direction_rank=4))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_EVENT_LAGGARD")

    def test_aac_rejects_inefficient_borrowed_peer_move(self):
        result = self.classify(
            self.decision(
                scenario="AAC",
                event_direction_rank=2,
                event_path_efficiency=0.077,
                event_standardized_displacement=0.67,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_INEFFICIENT_EVENT_PATH")

    def test_incomplete_snapshot_preserves_fail_closed_reason(self):
        result = self.classify(
            self.decision(
                reason="MISSING_SYNCHRONIZED_PEER_SNAPSHOT",
                peer_returns={},
                candidate_event_move=None,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "MISSING_SYNCHRONIZED_PEER_SNAPSHOT")


if __name__ == "__main__":
    unittest.main()
