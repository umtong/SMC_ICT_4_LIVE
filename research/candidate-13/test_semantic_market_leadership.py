from __future__ import annotations

import unittest

from market_leadership import LeadershipDecision
from semantic_market_leadership import (
    AAC_LAGGARD_TRANSFER,
    FAR_CAPITULATION_IDIOSYNCRATIC,
    FAR_CAPITULATION_SYNCHRONIZED,
    FAR_EXHAUSTION_LAGGARD,
    FAR_EXHAUSTION_QUORUM,
    FAR_EXHAUSTION_UNANIMOUS,
    FAR_NASCENT_TREND_RESUMPTION,
    semantic_decision,
)

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class SemanticLeadershipTests(unittest.TestCase):
    def decision(self, **updates):
        values = dict(
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
        values.update(updates)
        return LeadershipDecision(**values)

    def classify(self, decision):
        return semantic_decision(
            decision,
            symbol_count=4,
            severe_adverse_trend_score=-1.5,
            minimum_confirmation_impulse=1.0,
            minimum_event_efficiency=0.10,
            minimum_event_displacement=0.50,
        )

    def test_unanimous_common_auction(self):
        result = self.classify(self.decision())
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, FAR_EXHAUSTION_UNANIMOUS)

    def test_partial_quorum_requires_full_atr_candidate_event(self):
        peers = {"BTCUSDT": -0.004, "ETHUSDT": -0.003, "XRPUSDT": 0.0005}
        weak = self.classify(
            self.decision(peer_returns=peers, event_standardized_displacement=0.99),
        )
        strong = self.classify(
            self.decision(peer_returns=peers, event_standardized_displacement=1.01),
        )
        self.assertFalse(weak.approved)
        self.assertEqual(strong.reason, FAR_EXHAUSTION_QUORUM)

    def test_synchronized_capitulation(self):
        scores = {symbol: -1.8 for symbol in SYMBOLS}
        result = self.classify(
            self.decision(directional_trend_scores=scores, event_direction_rank=3),
        )
        self.assertEqual(result.reason, FAR_CAPITULATION_SYNCHRONIZED)

    def test_idiosyncratic_capitulation_requires_nonleader_full_atr_event(self):
        scores = {
            "BTCUSDT": -1.7,
            "ETHUSDT": -1.6,
            "SOLUSDT": -1.8,
            "XRPUSDT": -1.4,
        }
        peers = {"BTCUSDT": -0.003, "ETHUSDT": 0.001, "XRPUSDT": 0.002}
        result = self.classify(
            self.decision(
                directional_trend_scores=scores,
                peer_returns=peers,
                event_direction_rank=1,
                event_path_efficiency=0.30,
                event_standardized_displacement=1.40,
            ),
        )
        self.assertEqual(result.reason, FAR_CAPITULATION_IDIOSYNCRATIC)

    def test_laggard_must_replace_rank_with_full_atr_event(self):
        result = self.classify(
            self.decision(
                event_direction_rank=4,
                event_path_efficiency=0.20,
                event_standardized_displacement=1.20,
            ),
        )
        self.assertEqual(result.reason, FAR_EXHAUSTION_LAGGARD)

    def test_nascent_trend_resumption_is_not_generic_trend_following(self):
        scores = {
            "BTCUSDT": 0.20,
            "ETHUSDT": 0.10,
            "SOLUSDT": 0.30,
            "XRPUSDT": 0.70,
        }
        peers = {"BTCUSDT": -0.003, "ETHUSDT": -0.004, "XRPUSDT": 0.002}
        result = self.classify(
            self.decision(
                directional_trend_scores=scores,
                peer_returns=peers,
                confirmation_impulse=1.80,
                trailing_direction_rank=2,
                event_direction_rank=2,
            ),
        )
        self.assertEqual(result.reason, FAR_NASCENT_TREND_RESUMPTION)

        intermediate = dict(scores)
        intermediate["SOLUSDT"] = 0.65
        rejected = self.classify(
            self.decision(
                directional_trend_scores=intermediate,
                peer_returns=peers,
                confirmation_impulse=2.00,
                trailing_direction_rank=2,
                event_direction_rank=1,
            ),
        )
        self.assertFalse(rejected.approved)
        self.assertEqual(rejected.reason, "SEMANTIC_FAR_NOT_COUNTERTREND")

    def test_aac_laggard_transfer_cannot_also_be_trailing_laggard(self):
        scores = {symbol: 0.5 for symbol in SYMBOLS}
        peers = {"BTCUSDT": 0.003, "ETHUSDT": 0.002, "XRPUSDT": 0.001}
        accepted = self.classify(
            self.decision(
                scenario="AAC",
                direction="LONG",
                directional_trend_scores=scores,
                peer_returns=peers,
                event_direction_rank=4,
                trailing_direction_rank=3,
                event_path_efficiency=0.14,
                event_standardized_displacement=0.75,
            ),
        )
        rejected = self.classify(
            self.decision(
                scenario="AAC",
                direction="LONG",
                directional_trend_scores=scores,
                peer_returns=peers,
                event_direction_rank=4,
                trailing_direction_rank=4,
                event_path_efficiency=0.14,
                event_standardized_displacement=0.75,
            ),
        )
        self.assertEqual(accepted.reason, AAC_LAGGARD_TRANSFER)
        self.assertFalse(rejected.approved)

    def test_incomplete_snapshot_fails_closed(self):
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
