from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import Side
from market_v10 import (
    BROAD_RECLAIM,
    INSUFFICIENT_PEERS,
    ISOLATED,
    UNRESOLVED,
    PeerRangeObservation,
    classify_cross_sectional_raid,
)


def observation(
    symbol,
    *,
    side=Side.LONG,
    low=100.0,
    high=110.0,
    excursion_low=99.0,
    excursion_high=109.0,
    close=101.0,
):
    return PeerRangeObservation(
        symbol=symbol,
        side=side,
        range_low=low,
        range_high=high,
        excursion_low=excursion_low,
        excursion_high=excursion_high,
        close=close,
    )


class TestCrossSectionalRaidState(unittest.TestCase):
    def test_isolated_requires_majority_nonconfirmation_and_deepest_candidate(self):
        candidate = observation("BTCUSDT", excursion_low=97.0)
        peers = [
            observation("ETHUSDT", excursion_low=100.2),
            observation("SOLUSDT", excursion_low=100.1),
            observation("XRPUSDT", excursion_low=99.5),
        ]
        decision = classify_cross_sectional_raid(candidate=candidate, peers=peers)
        self.assertEqual(decision.state, ISOLATED)
        self.assertEqual(decision.nonconfirming_peers, ("ETHUSDT", "SOLUSDT"))
        self.assertIn("BTCUSDT", decision.deepest_symbols)

    def test_nonconfirming_majority_without_deepest_candidate_is_unresolved(self):
        candidate = observation("BTCUSDT", excursion_low=99.5)
        peers = [
            observation("ETHUSDT", excursion_low=100.2),
            observation("SOLUSDT", excursion_low=100.1),
            observation("XRPUSDT", excursion_low=97.0),
        ]
        decision = classify_cross_sectional_raid(candidate=candidate, peers=peers)
        self.assertEqual(decision.state, UNRESOLVED)

    def test_broad_reclaim_requires_majority_same_side_sweep_and_reclaim(self):
        candidate = observation("BTCUSDT", excursion_low=98.0)
        peers = [
            observation("ETHUSDT", excursion_low=99.0, close=101.0),
            observation("SOLUSDT", excursion_low=98.5, close=100.5),
            observation("XRPUSDT", excursion_low=100.2, close=101.0),
        ]
        decision = classify_cross_sectional_raid(candidate=candidate, peers=peers)
        self.assertEqual(decision.state, BROAD_RECLAIM)
        self.assertEqual(decision.reclaimed_swept_peers, ("ETHUSDT", "SOLUSDT"))

    def test_swept_peers_without_reclaim_are_not_broad_reversal(self):
        candidate = observation("BTCUSDT", excursion_low=98.0)
        peers = [
            observation("ETHUSDT", excursion_low=99.0, close=99.5),
            observation("SOLUSDT", excursion_low=98.5, close=99.0),
            observation("XRPUSDT", excursion_low=100.2, close=101.0),
        ]
        decision = classify_cross_sectional_raid(candidate=candidate, peers=peers)
        self.assertEqual(decision.state, UNRESOLVED)

    def test_three_distinct_peers_are_required(self):
        candidate = observation("BTCUSDT")
        peers = [observation("ETHUSDT"), observation("SOLUSDT")]
        decision = classify_cross_sectional_raid(candidate=candidate, peers=peers)
        self.assertEqual(decision.state, INSUFFICIENT_PEERS)

    def test_short_side_is_normalized_symmetrically(self):
        candidate = observation(
            "BTCUSDT",
            side=Side.SHORT,
            excursion_low=101.0,
            excursion_high=114.0,
            close=109.0,
        )
        peers = [
            observation("ETHUSDT", side=Side.SHORT, excursion_low=101.0, excursion_high=109.0, close=109.0),
            observation("SOLUSDT", side=Side.SHORT, excursion_low=101.0, excursion_high=109.5, close=109.0),
            observation("XRPUSDT", side=Side.SHORT, excursion_low=101.0, excursion_high=111.0, close=109.0),
        ]
        decision = classify_cross_sectional_raid(candidate=candidate, peers=peers)
        self.assertEqual(decision.state, ISOLATED)
        self.assertAlmostEqual(decision.candidate_penetration, 0.4)


if __name__ == "__main__":
    unittest.main()
