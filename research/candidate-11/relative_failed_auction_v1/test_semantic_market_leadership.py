from __future__ import annotations

import unittest

from market_leadership import LeadershipDecision
from semantic_market_leadership import semantic_decision, session_semantic_decision


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class RelativeFailedAuctionSemanticTests(unittest.TestCase):
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
            # SHORT signs returns by -1. Two positive raw peer returns therefore
            # mean the peer majority did not follow the local short repair.
            peer_returns={"BTCUSDT": 0.004, "ETHUSDT": 0.003, "XRPUSDT": -0.001},
            directional_returns={symbol: -0.01 for symbol in SYMBOLS},
            directional_trend_scores={
                "BTCUSDT": -1.19,
                "ETHUSDT": -0.15,
                "SOLUSDT": -1.32,
                "XRPUSDT": -0.11,
            },
            candidate_event_move=0.006,
            peer_event_median=-0.003,
            confirmation_impulse=-0.25,
            trailing_direction_rank=3,
            event_direction_rank=2,
            event_path_efficiency=0.12,
            event_standardized_displacement=0.86,
        )
        base.update(updates)
        return LeadershipDecision(**base)

    @staticmethod
    def classify_core(decision):
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

    def test_preserved_scdam_core_still_requires_peer_reclaim(self):
        result = self.classify_core(self.decision())
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM")

    def test_majority_peer_nonconfirmation_admits_completed_local_repair(self):
        result = self.classify_session(self.decision())
        self.assertTrue(result.approved)
        self.assertEqual(
            result.reason,
            "RELATIVE_FAR_I7_MAJORITY_PEER_NONCONFIRMATION",
        )

    def test_broad_unanimous_transfer_is_not_a_relative_failed_auction(self):
        result = self.classify_session(
            self.decision(
                peer_returns={
                    "BTCUSDT": -0.004,
                    "ETHUSDT": -0.003,
                    "XRPUSDT": -0.002,
                },
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "RELATIVE_FAR_I7_REQUIRES_MAJORITY_PEER_NONCONFIRMATION",
        )

    def test_one_nonconfirming_peer_is_insufficient(self):
        result = self.classify_session(
            self.decision(
                peer_returns={
                    "BTCUSDT": -0.004,
                    "ETHUSDT": -0.003,
                    "XRPUSDT": 0.002,
                },
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "RELATIVE_FAR_I7_REQUIRES_MAJORITY_PEER_NONCONFIRMATION",
        )

    def test_local_market_must_already_be_repairing(self):
        result = self.classify_session(self.decision(candidate_event_move=-0.001))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "RELATIVE_FAR_I7_WITHOUT_LOCAL_REPAIR")

    def test_local_repair_must_remain_path_efficient(self):
        result = self.classify_session(self.decision(event_path_efficiency=0.09))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "RELATIVE_FAR_I7_INEFFICIENT_LOCAL_REPAIR")

    def test_local_repair_must_remain_displaced(self):
        result = self.classify_session(
            self.decision(event_standardized_displacement=0.49),
        )
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "RELATIVE_FAR_I7_INSUFFICIENT_LOCAL_DISPLACEMENT",
        )

    def test_local_repair_must_be_top_half_price_discovery(self):
        result = self.classify_session(self.decision(event_direction_rank=3))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "RELATIVE_FAR_I7_LOCAL_REPAIR_NOT_TOP_HALF")

    def test_completed_route_replaces_terminal_one_minute_impulse(self):
        result = self.classify_session(self.decision(confirmation_impulse=-9.0))
        self.assertTrue(result.approved)
        self.assertEqual(
            result.reason,
            "RELATIVE_FAR_I7_MAJORITY_PEER_NONCONFIRMATION",
        )

    def test_severe_adverse_local_and_market_auction_remains_rejected(self):
        result = self.classify_session(
            self.decision(
                directional_trend_scores={symbol: -2.0 for symbol in SYMBOLS},
            ),
        )
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "RELATIVE_FAR_I7_UNRESOLVED_SEVERE_ADVERSE_AUCTION",
        )

    def test_aac_is_outside_the_candidate_family(self):
        result = self.classify_session(self.decision(scenario="AAC"))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "RELATIVE_FAR_I7_ONLY")

    def test_incomplete_snapshot_fails_closed_with_measurement_reason(self):
        result = self.classify_session(
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
