from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from logic import LogicConfig

from c10_v39_overlay import entry_auction_acceptance_enabled
from c10_v39_overlay import evaluate_entry_auction
from c10_v39_state import EntryAuctionAcceptanceEngine


class V39EntryAuctionContractTest(unittest.TestCase):
    def test_long_accepts_only_at_or_above_passive_boundary(self) -> None:
        accepted = evaluate_entry_auction(
            direction="LONG",
            completed_close=1065.68,
            entry_boundary=1065.53,
        )
        failed = evaluate_entry_auction(
            direction="LONG",
            completed_close=2606.41,
            entry_boundary=2607.776,
        )
        self.assertTrue(accepted.accepted)
        self.assertAlmostEqual(accepted.distance_from_boundary, 0.15)
        self.assertFalse(failed.accepted)
        self.assertLess(failed.distance_from_boundary, 0.0)

    def test_short_accepts_only_at_or_below_passive_boundary(self) -> None:
        winner = evaluate_entry_auction(
            direction="SHORT",
            completed_close=25.151,
            entry_boundary=25.154,
        )
        failed = evaluate_entry_auction(
            direction="SHORT",
            completed_close=68877.3,
            entry_boundary=68778.66,
        )
        self.assertTrue(winner.accepted)
        self.assertGreater(winner.distance_from_boundary, 0.0)
        self.assertFalse(failed.accepted)
        self.assertLess(failed.distance_from_boundary, 0.0)

    def test_zero_buffer_boundary_is_exactly_accepted(self) -> None:
        long = evaluate_entry_auction(
            direction="LONG",
            completed_close=100.0,
            entry_boundary=100.0,
        )
        short = evaluate_entry_auction(
            direction="SHORT",
            completed_close=100.0,
            entry_boundary=100.0,
        )
        self.assertTrue(long.accepted)
        self.assertTrue(short.accepted)

    def test_environment_ablation_is_exact(self) -> None:
        with patch.dict(os.environ, {"C10_V39_ENTRY_AUCTION_ACCEPTANCE": "0"}):
            self.assertFalse(entry_auction_acceptance_enabled())
        with patch.dict(os.environ, {"C10_V39_ENTRY_AUCTION_ACCEPTANCE": "1"}):
            self.assertTrue(entry_auction_acceptance_enabled())


class V39StateEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = EntryAuctionAcceptanceEngine(
            LogicConfig(),
            "TEST-PERP.BINANCE",
        )
        self.engine.active_trade_id = "SCENARIO-1"
        self.engine.active_trade_state = "POSITION"

    def test_accepted_fill_bar_changes_risk_ownership_state(self) -> None:
        self.engine.mark_entry_auction_evaluated(
            fill_ts_ns=100,
            observed_ts_ns=100,
            direction="LONG",
            boundary=100.0,
            completed_close=100.2,
            accepted=True,
            distance_from_boundary=0.2,
        )
        event = self.engine.events[-1]
        self.assertEqual(event.event_type, "ENTRY_AUCTION_ACCEPTANCE_CONFIRMED")
        self.assertEqual(event.previous_state, "POSITION")
        self.assertEqual(event.next_state, "ENTRY_AUCTION_ACCEPTED")
        self.assertEqual(
            event.reason_code,
            "COMPLETED_FILL_BAR_HELD_PREDICTED_SIDE_OF_PASSIVE_BOUNDARY",
        )
        self.assertEqual(
            self.engine.active_trade_state,
            "ENTRY_AUCTION_ACCEPTED",
        )

    def test_failed_fill_bar_enters_exit_pending_state(self) -> None:
        self.engine.mark_entry_auction_evaluated(
            fill_ts_ns=100,
            observed_ts_ns=100,
            direction="SHORT",
            boundary=100.0,
            completed_close=100.3,
            accepted=False,
            distance_from_boundary=-0.3,
        )
        event = self.engine.events[-1]
        self.assertEqual(event.event_type, "ENTRY_AUCTION_HOLD_FAILED")
        self.assertEqual(
            event.next_state,
            "ENTRY_AUCTION_FAILED_EXIT_PENDING",
        )
        self.assertEqual(
            event.reason_code,
            "COMPLETED_FILL_BAR_FAILED_TO_HOLD_PASSIVE_BOUNDARY",
        )


if __name__ == "__main__":
    unittest.main()
