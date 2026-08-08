from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from diagnose_parent_auction_state import (  # noqa: E402
    ParentAuctionState,
    build_parent_state_history,
    parent_state_strictly_before,
    prepare_complete_auctions,
    reversal_context,
)


class ParentAuctionStateTests(unittest.TestCase):
    @staticmethod
    def _minute_frame() -> pd.DataFrame:
        index = pd.date_range(
            "2025-01-01 00:00:59.999000+00:00",
            periods=120,
            freq="min",
        )
        records = []
        specifications = (
            (95.0, 100.0, 90.0),
            (101.0, 102.0, 96.0),
            # Persist above the accepted 100 boundary without making another
            # close beyond the immediately preceding 102 high.  The lower wick
            # also ensures the next 99 close is a boundary reclaim, not a new
            # bearish close beyond this auction's low.
            (101.5, 103.0, 98.0),
            (99.0, 104.0, 98.0),
        )
        for bucket, (close, high, low) in enumerate(specifications):
            for _ in range(30):
                records.append(
                    {
                        "open": close,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": 1.0 + bucket,
                    }
                )
        return pd.DataFrame.from_records(records, index=index)

    def test_complete_auctions_activate_persist_and_release(self) -> None:
        auctions = prepare_complete_auctions(self._minute_frame())
        self.assertEqual(len(auctions.index), 4)
        history = build_parent_state_history(auctions)
        self.assertIsNone(history[0]["state"])
        self.assertEqual(history[1]["activation"], "BULLISH_OUTSIDE_ACCEPTANCE_WITH_VALUE_MIGRATION")
        self.assertEqual(history[1]["state"]["direction"], "BULLISH")
        self.assertAlmostEqual(history[1]["state"]["accepted_boundary"], 100.0)
        self.assertEqual(history[2]["state"]["age_buckets"], 1)
        self.assertEqual(history[3]["release"], "ACCEPTED_BOUNDARY_RECLAIMED")
        self.assertIsNone(history[3]["state"])

    def test_state_lookup_is_strictly_prior(self) -> None:
        auctions = prepare_complete_auctions(self._minute_frame())
        history = build_parent_state_history(auctions)
        activation_end = int(auctions.iloc[1]["end_ns"])
        self.assertIsNone(parent_state_strictly_before(history, activation_end))
        state = parent_state_strictly_before(history, activation_end + 1)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.direction, "BULLISH")

    def test_opposite_acceptance_atomically_transfers_state(self) -> None:
        auctions = pd.DataFrame.from_records(
            [
                {"end_ns": 1, "high": 100.0, "low": 90.0, "close": 95.0, "value": 95.0},
                {"end_ns": 2, "high": 103.0, "low": 96.0, "close": 102.0, "value": 101.0},
                {"end_ns": 3, "high": 99.0, "low": 85.0, "close": 86.0, "value": 88.0},
            ]
        )
        history = build_parent_state_history(auctions)
        self.assertEqual(history[1]["state"]["direction"], "BULLISH")
        self.assertEqual(history[2]["state"]["direction"], "BEARISH")
        self.assertEqual(history[2]["activation"], "BEARISH_OUTSIDE_ACCEPTANCE_WITH_VALUE_MIGRATION")
        self.assertAlmostEqual(history[2]["state"]["accepted_boundary"], 96.0)

    def test_reversal_context_is_symmetric(self) -> None:
        bullish = ParentAuctionState(
            direction="BULLISH",
            accepted_boundary=100.0,
            activated_ns=10,
            source_bucket_end_ns=9,
            source_value=99.0,
            current_value=101.0,
            age_buckets=0,
        )
        bearish = ParentAuctionState(
            direction="BEARISH",
            accepted_boundary=90.0,
            activated_ns=20,
            source_bucket_end_ns=19,
            source_value=91.0,
            current_value=89.0,
            age_buckets=0,
        )
        self.assertEqual(reversal_context("SHORT", bullish), "COUNTER_INITIATIVE")
        self.assertEqual(reversal_context("LONG", bullish), "WITH_INITIATIVE")
        self.assertEqual(reversal_context("LONG", bearish), "COUNTER_INITIATIVE")
        self.assertEqual(reversal_context("SHORT", bearish), "WITH_INITIATIVE")
        self.assertEqual(reversal_context("LONG", None), "BALANCE_OR_UNRESOLVED")


if __name__ == "__main__":
    unittest.main()
