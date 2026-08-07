from __future__ import annotations

import unittest

from market_leadership import LeadershipDecision
from semantic_market_leadership import semantic_decision


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


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
            directional_returns={symbol: -0.01 for symbol in SYMBOLS},
            directional_trend_scores={
                "BTCUSDT": -1.19,
                "ETHUSDT": -0.15,
                "SOLUSDT": -1.32,
                "XRPUSDT": -0.11,
            },
            candidate_event_move=0.006,
            peer_event_median=0.003,
            confirmation_impulse=2.10,
            trailing_direction_rank=3,
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

    def test_far_accepts_dominant_peer_quorum_for_strong_follower(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0005}
        result = self.classify(self.decision(peer_returns=peers))
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_DOMINANT_PEER_QUORUM")

    def test_far_quorum_rejects_incoherent_completed_auction(self):
        peers = {"BTCUSDT": -0.004, "SOLUSDT": -0.003, "XRPUSDT": 0.0005}
        scores = {
            "BTCUSDT": -0.85,
            "ETHUSDT": -0.69,
            "SOLUSDT": 0.05,
            "XRPUSDT": -0.14,
        }
        result = self.classify(
            self.decision(
                symbol="ETHUSDT",
                peer_returns=peers,
                directional_trend_scores=scores,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_QUORUM_REQUIRES_COHERENT_ADVERSE_AUCTION",
        )

    def test_far_rejects_material_dissent(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0045}
        result = self.classify(self.decision(peer_returns=peers))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM")

    def test_far_quorum_cannot_validate_liquidity_leader(self):
        peers = {"ETHUSDT": -0.004, "SOLUSDT": -0.003, "XRPUSDT": 0.0005}
        result = self.classify(
            self.decision(symbol="BTCUSDT", peer_returns=peers, event_direction_rank=1),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_QUORUM_CANNOT_USE_LIQUIDITY_LEADER")

    def test_far_quorum_requires_efficient_local_path(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0005}
        result = self.classify(
            self.decision(peer_returns=peers, event_path_efficiency=0.09),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_QUORUM_INEFFICIENT_LOCAL_PATH")

    def test_far_quorum_requires_standardized_local_displacement(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0005}
        result = self.classify(
            self.decision(peer_returns=peers, event_standardized_displacement=0.49),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_QUORUM_INSUFFICIENT_LOCAL_DISPLACEMENT")

    def test_far_rejects_aligned_trailing_reclaim(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_NOT_COUNTERTREND")

    def test_far_rejects_event_laggard(self):
        result = self.classify(self.decision(event_direction_rank=4))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_EVENT_LAGGARD")

    def test_far_rejects_severely_unresolved_countertrend(self):
        scores = {symbol: -2.0 for symbol in SYMBOLS}
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")

    def test_aac_accepts_aligned_efficient_nonlaggard_move(self):
        scores = {symbol: value for symbol, value in zip(SYMBOLS, (0.10, 0.42, 0.23, 0.40), strict=True)}
        result = self.classify(
            self.decision(
                scenario="AAC",
                event_direction_rank=3,
                directional_trend_scores=scores,
            ),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_ALIGNED_SYNCHRONIZED_NONLAGGARD")

    def test_aac_rejects_countertrend_attempted_acceptance(self):
        result = self.classify(self.decision(scenario="AAC", event_direction_rank=1))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_REQUIRES_ALIGNED_TRAILING_AUCTION")

    def test_aac_rejects_last_mover(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(
            self.decision(
                scenario="AAC",
                event_direction_rank=4,
                directional_trend_scores=scores,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_EVENT_LAGGARD")

    def test_aac_rejects_inefficient_borrowed_peer_move(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(
            self.decision(
                scenario="AAC",
                event_direction_rank=2,
                directional_trend_scores=scores,
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
