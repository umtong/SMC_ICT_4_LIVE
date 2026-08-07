from __future__ import annotations

import unittest

from market_leadership import LeadershipDecision
from semantic_market_leadership import (
    FAR_EXHAUSTION_QUORUM,
    FAR_EXHAUSTION_UNANIMOUS,
    FAR_IDIOSYNCRATIC,
    FAR_ROTATION_DISPLACEMENT,
    FAR_ROTATION_UNANIMOUS,
    semantic_decision,
)


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

    def test_far_exhaustion_unanimous(self):
        result = self.classify(self.decision())
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, FAR_EXHAUSTION_UNANIMOUS)

    def test_far_exhaustion_dominant_quorum_for_strong_follower(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0005}
        result = self.classify(self.decision(peer_returns=peers))
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, FAR_EXHAUSTION_QUORUM)

    def test_far_exhaustion_rejects_material_dissent(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0045}
        result = self.classify(self.decision(peer_returns=peers))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_EXHAUSTION_REQUIRES_PEER_QUORUM")

    def test_far_exhaustion_quorum_cannot_validate_liquidity_leader(self):
        peers = {"ETHUSDT": -0.004, "SOLUSDT": -0.003, "XRPUSDT": 0.0005}
        result = self.classify(
            self.decision(symbol="BTCUSDT", peer_returns=peers, event_direction_rank=1),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_QUORUM_CANNOT_USE_LIQUIDITY_LEADER")

    def test_far_exhaustion_quorum_requires_local_event_quality(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0005}
        result = self.classify(
            self.decision(peer_returns=peers, event_path_efficiency=0.09),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_QUORUM_REQUIRES_LOCAL_EVENT_QUALITY")

    def test_far_split_prior_unanimous_rotation(self):
        scores = {
            "BTCUSDT": -0.85,
            "ETHUSDT": -0.69,
            "SOLUSDT": -0.44,
            "XRPUSDT": 0.33,
        }
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, FAR_ROTATION_UNANIMOUS)

    def test_far_split_prior_weak_impulse_can_use_event_displacement(self):
        scores = {
            "BTCUSDT": -0.68,
            "ETHUSDT": 0.72,
            "SOLUSDT": 0.34,
            "XRPUSDT": -0.95,
        }
        result = self.classify(
            self.decision(
                symbol="BTCUSDT",
                directional_trend_scores=scores,
                confirmation_impulse=0.72,
                event_direction_rank=3,
                event_path_efficiency=0.15,
                event_standardized_displacement=0.91,
            ),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, FAR_ROTATION_DISPLACEMENT)

    def test_far_split_prior_weak_impulse_without_event_quality_rejected(self):
        scores = {
            "BTCUSDT": -0.68,
            "ETHUSDT": 0.72,
            "SOLUSDT": 0.34,
            "XRPUSDT": -0.95,
        }
        result = self.classify(
            self.decision(
                symbol="BTCUSDT",
                directional_trend_scores=scores,
                confirmation_impulse=0.72,
                event_direction_rank=3,
                event_path_efficiency=0.05,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_ROTATION_REQUIRES_IMPULSE_OR_EVENT_QUALITY",
        )

    def test_far_split_prior_partial_quorum_is_not_transfer(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0005}
        scores = {
            "BTCUSDT": -0.85,
            "ETHUSDT": -0.69,
            "SOLUSDT": -0.44,
            "XRPUSDT": 0.33,
        }
        result = self.classify(
            self.decision(peer_returns=peers, directional_trend_scores=scores),
        )
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_SPLIT_AUCTION_REQUIRES_UNANIMOUS_TRANSFER",
        )

    def test_far_idiosyncratic_price_discovery(self):
        peers = {"BTCUSDT": 0.00014, "ETHUSDT": 0.00039, "XRPUSDT": -0.00101}
        scores = {
            "BTCUSDT": -0.77,
            "ETHUSDT": -0.27,
            "SOLUSDT": -0.90,
            "XRPUSDT": 0.30,
        }
        result = self.classify(
            self.decision(
                peer_returns=peers,
                directional_trend_scores=scores,
                event_direction_rank=1,
                event_path_efficiency=0.61,
                event_standardized_displacement=1.19,
            ),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, FAR_IDIOSYNCRATIC)

    def test_far_idiosyncratic_requires_event_lead(self):
        peers = {"BTCUSDT": 0.00014, "ETHUSDT": 0.00039, "XRPUSDT": -0.00101}
        scores = {
            "BTCUSDT": -0.77,
            "ETHUSDT": -0.27,
            "SOLUSDT": -0.90,
            "XRPUSDT": 0.30,
        }
        result = self.classify(
            self.decision(
                peer_returns=peers,
                directional_trend_scores=scores,
                event_direction_rank=2,
                event_path_efficiency=0.61,
                event_standardized_displacement=1.19,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_IDIOSYNCRATIC_REQUIRES_EVENT_LEAD")

    def test_far_rejects_trend_following_state(self):
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
        scores = {
            symbol: value
            for symbol, value in zip(SYMBOLS, (0.10, 0.42, 0.23, 0.40), strict=True)
        }
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
