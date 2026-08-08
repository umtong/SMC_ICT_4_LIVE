from __future__ import annotations

import unittest

from market_leadership import LeadershipDecision
from semantic_market_leadership import semantic_decision, session_semantic_decision


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class Candidate14PreservedCoreTests(unittest.TestCase):
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

    @staticmethod
    def classify(decision):
        return semantic_decision(
            decision,
            symbol_count=4,
            severe_adverse_trend_score=-1.5,
            minimum_confirmation_impulse=1.0,
            minimum_event_efficiency=0.10,
            minimum_event_displacement=0.50,
        )

    @staticmethod
    def classify_session(decision):
        return session_semantic_decision(
            decision,
            symbol_count=4,
            severe_adverse_trend_score=-1.5,
            minimum_confirmation_impulse=1.0,
            minimum_event_efficiency=0.10,
            minimum_event_displacement=0.50,
        )

    def test_unanimous_moderate_countertrend_core(self):
        result = self.classify(self.decision())
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS")

    def test_dominant_peer_quorum_core(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0005}
        result = self.classify(self.decision(peer_returns=peers))
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_DOMINANT_PEER_QUORUM")

    def test_material_peer_dissent_fails_closed(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0045}
        result = self.classify(self.decision(peer_returns=peers))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM")

    def test_quorum_cannot_use_liquidity_leader(self):
        peers = {"ETHUSDT": -0.004, "SOLUSDT": -0.003, "XRPUSDT": 0.0005}
        result = self.classify(
            self.decision(symbol="BTCUSDT", peer_returns=peers, event_direction_rank=1),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_QUORUM_CANNOT_USE_LIQUIDITY_LEADER")

    def test_weak_terminal_displacement_is_not_replaced_in_scdam_core(self):
        result = self.classify(
            self.decision(
                confirmation_impulse=0.72,
                event_path_efficiency=0.50,
                event_standardized_displacement=1.20,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_WEAK_LOCAL_DISPLACEMENT")

    def test_generic_trend_resumption_is_rejected_in_scdam_core(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_NOT_COUNTERTREND")

    def test_generic_laggard_is_rejected(self):
        result = self.classify(self.decision(event_direction_rank=4))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_EVENT_LAGGARD")

    def test_severely_unresolved_adverse_auction_is_rejected(self):
        scores = {symbol: -2.0 for symbol in SYMBOLS}
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")

    def test_aac_aligned_synchronized_nonlaggard_core(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(
            self.decision(
                scenario="AAC",
                direction="LONG",
                peer_returns={"BTCUSDT": 0.004, "ETHUSDT": 0.003, "XRPUSDT": 0.002},
                directional_trend_scores=scores,
                event_path_efficiency=0.20,
                event_standardized_displacement=1.0,
            ),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_ALIGNED_SYNCHRONIZED_NONLAGGARD")

    def test_incomplete_snapshot_preserves_base_fail_closed_reason(self):
        result = self.classify(
            self.decision(
                reason="MISSING_SYNCHRONIZED_PEER_SNAPSHOT",
                peer_returns={},
                candidate_event_move=None,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "MISSING_SYNCHRONIZED_PEER_SNAPSHOT")

    def test_i7_trend_resumption_uses_complete_path_not_last_one_minute_bar(self):
        scores = {
            "BTCUSDT": 0.80,
            "ETHUSDT": 1.24,
            "SOLUSDT": 0.40,
            "XRPUSDT": 1.14,
        }
        decision = self.decision(
            symbol="BTCUSDT",
            peer_returns={"ETHUSDT": -0.0018, "SOLUSDT": -0.0013, "XRPUSDT": -0.0018},
            directional_trend_scores=scores,
            confirmation_impulse=-1.88,
            trailing_direction_rank=2,
            event_direction_rank=2,
            event_path_efficiency=0.20,
            event_standardized_displacement=1.25,
        )
        core = self.classify(decision)
        session = self.classify_session(decision)
        self.assertFalse(core.approved)
        self.assertEqual(core.reason, "SEMANTIC_FAR_WEAK_LOCAL_DISPLACEMENT")
        self.assertTrue(session.approved)
        self.assertEqual(session.reason, "SEMANTIC_FAR_I7_TREND_RESUMPTION")

    def test_i7_countertrend_complete_path_can_substitute_for_terminal_impulse(self):
        decision = self.decision(
            symbol="BTCUSDT",
            peer_returns={"ETHUSDT": -0.002, "SOLUSDT": -0.003, "XRPUSDT": -0.001},
            confirmation_impulse=0.2,
            trailing_direction_rank=4,
            event_direction_rank=2,
            event_path_efficiency=0.30,
            event_standardized_displacement=1.10,
        )
        result = self.classify_session(decision)
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_I7_COUNTERTREND_TRANSFER")

    def test_i7_final_market_laggard_remains_rejected(self):
        decision = self.decision(
            symbol="BTCUSDT",
            peer_returns={"ETHUSDT": -0.0015, "SOLUSDT": -0.0018, "XRPUSDT": -0.0021},
            confirmation_impulse=-0.42,
            trailing_direction_rank=4,
            event_direction_rank=4,
            event_path_efficiency=0.18,
            event_standardized_displacement=0.71,
        )
        result = self.classify_session(decision)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_I7_EVENT_NOT_TOP_HALF")

    def test_i7_path_must_remain_efficient_and_displaced(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        decision = self.decision(
            symbol="BTCUSDT",
            peer_returns={"ETHUSDT": -0.002, "SOLUSDT": -0.003, "XRPUSDT": -0.001},
            directional_trend_scores=scores,
            confirmation_impulse=-0.5,
            trailing_direction_rank=1,
            event_direction_rank=1,
            event_path_efficiency=0.09,
            event_standardized_displacement=1.0,
        )
        result = self.classify_session(decision)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_I7_INEFFICIENT_CAUSAL_PATH")

    def test_session_aac_does_not_relax_final_impulse(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        decision = self.decision(
            symbol="BTCUSDT",
            scenario="AAC",
            direction="LONG",
            peer_returns={"ETHUSDT": 0.003, "SOLUSDT": 0.004, "XRPUSDT": 0.002},
            directional_trend_scores=scores,
            confirmation_impulse=-0.9,
            trailing_direction_rank=1,
            event_direction_rank=2,
            event_path_efficiency=0.35,
            event_standardized_displacement=1.5,
        )
        result = self.classify_session(decision)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_WEAK_LOCAL_DISPLACEMENT")


if __name__ == "__main__":
    unittest.main()
