from __future__ import annotations

import unittest

from market_leadership import LeadershipDecision
from semantic_market_leadership import semantic_decision


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class Candidate14SemanticLeadershipTests(unittest.TestCase):
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

    def test_far_countertrend_common_auction(self):
        result = self.classify(self.decision())
        self.assertTrue(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_COUNTERTREND_REVERSAL_COMMON_IMPULSE",
        )

    def test_far_trend_resumption_is_a_distinct_valid_branch(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertTrue(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_TREND_RESUMPTION_COMMON_IMPULSE",
        )

    def test_far_accepts_path_when_terminal_one_minute_impulse_is_weak(self):
        result = self.classify(self.decision(confirmation_impulse=0.25))
        self.assertTrue(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_COUNTERTREND_REVERSAL_COMMON_PATH",
        )

    def test_far_originator_can_lead_before_two_peers(self):
        peers = {"BTCUSDT": -0.002, "ETHUSDT": 0.001, "XRPUSDT": 0.0005}
        result = self.classify(
            self.decision(peer_returns=peers, event_direction_rank=1),
        )
        self.assertTrue(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_COUNTERTREND_REVERSAL_ORIGINATOR_TRANSFER",
        )

    def test_far_rejects_material_peer_opposition_without_originator_role(self):
        peers = {"BTCUSDT": -0.001, "ETHUSDT": -0.001, "XRPUSDT": 0.004}
        result = self.classify(self.decision(peer_returns=peers, event_direction_rank=2))
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_PEER_OPPOSITION_DOMINATES_TRANSFER",
        )

    def test_far_laggard_transfer_requires_unanimous_peers_and_local_path(self):
        result = self.classify(self.decision(event_direction_rank=4))
        self.assertTrue(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_COUNTERTREND_REVERSAL_LAGGARD_TRANSFER",
        )

    def test_far_laggard_with_divided_peers_fails_closed(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0002}
        result = self.classify(
            self.decision(peer_returns=peers, event_direction_rank=4),
        )
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_LAGGARD_WITHOUT_UNANIMOUS_TRANSFER",
        )

    def test_far_severe_unresolved_adverse_auction_still_rejected(self):
        scores = {symbol: -2.0 for symbol in SYMBOLS}
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")

    def test_far_mixed_trailing_auction_fails_closed(self):
        scores = {
            "BTCUSDT": -0.4,
            "ETHUSDT": -0.1,
            "SOLUSDT": 0.3,
            "XRPUSDT": 0.2,
        }
        result = self.classify(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_MIXED_TRAILING_AUCTION")

    def test_aac_common_repricing(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(
            self.decision(scenario="AAC", directional_trend_scores=scores),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_COMMON_REPRICING")

    def test_aac_path_can_replace_a_single_weak_terminal_bar(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(
            self.decision(
                scenario="AAC",
                directional_trend_scores=scores,
                confirmation_impulse=0.2,
            ),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_COMMON_PATH_CONFIRMATION")

    def test_aac_originator_transfer(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        peers = {"BTCUSDT": -0.002, "ETHUSDT": 0.001, "XRPUSDT": 0.0005}
        result = self.classify(
            self.decision(
                scenario="AAC",
                directional_trend_scores=scores,
                peer_returns=peers,
                event_direction_rank=1,
            ),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_ORIGINATOR_TRANSFER")

    def test_aac_laggard_transfer_after_unanimous_peer_move(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(
            self.decision(
                scenario="AAC",
                directional_trend_scores=scores,
                event_direction_rank=4,
            ),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_LAGGARD_TRANSFER")

    def test_aac_requires_trailing_alignment(self):
        result = self.classify(self.decision(scenario="AAC"))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_REQUIRES_ALIGNED_TRAILING_AUCTION")

    def test_aac_requires_efficient_path(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        result = self.classify(
            self.decision(
                scenario="AAC",
                directional_trend_scores=scores,
                event_path_efficiency=0.09,
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_INEFFICIENT_EVENT_PATH")

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


if __name__ == "__main__":
    unittest.main()
