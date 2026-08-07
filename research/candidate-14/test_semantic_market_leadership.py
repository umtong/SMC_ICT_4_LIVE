from __future__ import annotations

import unittest

from market_leadership import LeadershipDecision
from semantic_market_leadership import semantic_decision


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class Candidate14DevelopmentV2LeadershipTests(unittest.TestCase):
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
            peer_returns={"BTCUSDT": -0.0056, "ETHUSDT": -0.0048, "XRPUSDT": -0.0042},
            directional_returns={symbol: -0.01 for symbol in SYMBOLS},
            directional_trend_scores={
                "BTCUSDT": -1.1887,
                "ETHUSDT": -0.1502,
                "SOLUSDT": -1.3224,
                "XRPUSDT": -0.1118,
            },
            candidate_event_move=0.0053,
            peer_event_median=0.0048,
            confirmation_impulse=2.097,
            trailing_direction_rank=4,
            event_direction_rank=2,
            event_path_efficiency=0.0157,
            event_standardized_displacement=0.1389,
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

    def test_candidate13_unanimous_core_is_preserved(self):
        result = self.classify(self.decision())
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS")

    def test_candidate13_dominant_quorum_core_is_preserved(self):
        result = self.classify(
            self.decision(
                symbol="ETHUSDT",
                peer_returns={"BTCUSDT": -0.0043, "SOLUSDT": -0.0091, "XRPUSDT": 0.00067},
                directional_trend_scores={
                    "BTCUSDT": -0.4506,
                    "ETHUSDT": -1.2630,
                    "SOLUSDT": -0.9416,
                    "XRPUSDT": -0.7556,
                },
                event_direction_rank=2,
                event_path_efficiency=0.2765,
                event_standardized_displacement=1.5897,
                confirmation_impulse=1.1401,
            ),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_DOMINANT_PEER_QUORUM")

    def test_liquidity_leader_catchup_uses_peer_prior_lead_and_local_path(self):
        result = self.classify(
            self.decision(
                symbol="BTCUSDT",
                leader="BTCUSDT",
                direction="LONG",
                peer_returns={"ETHUSDT": 0.00312, "SOLUSDT": 0.00459, "XRPUSDT": 0.00520},
                directional_trend_scores={
                    "BTCUSDT": -0.6785,
                    "ETHUSDT": 0.7195,
                    "SOLUSDT": 0.3354,
                    "XRPUSDT": -0.9532,
                },
                candidate_event_move=0.00369,
                confirmation_impulse=0.7191,
                trailing_direction_rank=3,
                event_direction_rank=3,
                event_path_efficiency=0.1459,
                event_standardized_displacement=0.9095,
            ),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_LIQUIDITY_LEADER_CATCHUP")

    def test_nonleader_path_only_reversal_is_not_relaxed(self):
        result = self.classify(
            self.decision(
                symbol="XRPUSDT",
                confirmation_impulse=0.615,
                event_path_efficiency=0.564,
                event_standardized_displacement=1.558,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_WEAK_LOCAL_DISPLACEMENT")

    def test_leader_catchup_requires_majority_of_peers_already_aligned(self):
        result = self.classify(
            self.decision(
                symbol="BTCUSDT",
                leader="BTCUSDT",
                direction="LONG",
                peer_returns={"ETHUSDT": 0.003, "SOLUSDT": 0.004, "XRPUSDT": 0.005},
                directional_trend_scores={
                    "BTCUSDT": -0.6,
                    "ETHUSDT": 0.4,
                    "SOLUSDT": -0.3,
                    "XRPUSDT": -0.5,
                },
                confirmation_impulse=0.7,
                trailing_direction_rank=3,
                event_direction_rank=2,
                event_path_efficiency=0.2,
                event_standardized_displacement=1.0,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_WEAK_LOCAL_DISPLACEMENT")

    def test_leader_catchup_requires_efficient_displaced_path(self):
        result = self.classify(
            self.decision(
                symbol="BTCUSDT",
                leader="BTCUSDT",
                direction="LONG",
                peer_returns={"ETHUSDT": 0.003, "SOLUSDT": 0.004, "XRPUSDT": 0.005},
                directional_trend_scores={
                    "BTCUSDT": -0.6,
                    "ETHUSDT": 0.4,
                    "SOLUSDT": 0.3,
                    "XRPUSDT": -0.5,
                },
                confirmation_impulse=0.7,
                trailing_direction_rank=3,
                event_direction_rank=2,
                event_path_efficiency=0.09,
                event_standardized_displacement=1.0,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_WEAK_LOCAL_DISPLACEMENT")

    def test_generic_trend_resumption_remains_rejected(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_NOT_COUNTERTREND")

    def test_generic_originator_with_peer_disagreement_remains_rejected(self):
        result = self.classify(
            self.decision(
                symbol="XRPUSDT",
                peer_returns={"BTCUSDT": 0.0012, "ETHUSDT": -0.00117, "SOLUSDT": 0.00124},
                event_direction_rank=1,
                event_path_efficiency=0.36,
                event_standardized_displacement=1.60,
                confirmation_impulse=4.12,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM")

    def test_event_laggard_remains_rejected(self):
        result = self.classify(self.decision(event_direction_rank=4))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_EVENT_LAGGARD")

    def test_severe_adverse_auction_remains_rejected(self):
        scores = {symbol: -2.0 for symbol in SYMBOLS}
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")

    def test_aac_core_is_preserved(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(
            self.decision(
                symbol="BTCUSDT",
                scenario="AAC",
                direction="LONG",
                peer_returns={"ETHUSDT": 0.002, "SOLUSDT": 0.003, "XRPUSDT": 0.001},
                directional_trend_scores=scores,
                event_path_efficiency=0.2,
                event_standardized_displacement=1.0,
            ),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_ALIGNED_SYNCHRONIZED_NONLAGGARD")

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
