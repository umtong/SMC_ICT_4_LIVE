from __future__ import annotations

from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from initiative_auction import InitiativeAuctionGate  # noqa: E402
from model import Direction  # noqa: E402


class InitiativeAuctionGateTests(unittest.TestCase):
    def test_failed_short_reversal_owns_all_short_fades_in_leg(self) -> None:
        gate = InitiativeAuctionGate()
        state = gate.accept_failed_reversal(
            blocked_reversal_direction=Direction.SHORT,
            opposing_delivery_price=98.0,
            source_scenario_id="failed-short",
            accepted_source_level=101.0,
            accepted_at_ns=10,
        )
        self.assertEqual(state.initiative_direction, Direction.LONG)
        self.assertTrue(gate.is_blocked(Direction.SHORT))
        self.assertFalse(gate.is_blocked(Direction.LONG))
        self.assertEqual(gate.observe_close(98.01), ())
        self.assertEqual(gate.observe_close(98.0), (state,))
        self.assertFalse(gate.is_blocked(Direction.SHORT))

    def test_failed_long_reversal_is_symmetric(self) -> None:
        gate = InitiativeAuctionGate()
        state = gate.accept_failed_reversal(
            blocked_reversal_direction=Direction.LONG,
            opposing_delivery_price=102.0,
            source_scenario_id="failed-long",
            accepted_source_level=99.0,
            accepted_at_ns=20,
        )
        self.assertEqual(state.initiative_direction, Direction.SHORT)
        self.assertEqual(gate.observe_close(101.99), ())
        self.assertEqual(gate.observe_close(102.0), (state,))

    def test_counter_acceptance_transfers_initiative_without_time_expiry(self) -> None:
        gate = InitiativeAuctionGate()
        state = gate.accept_failed_reversal(
            blocked_reversal_direction=Direction.SHORT,
            opposing_delivery_price=90.0,
            source_scenario_id="old-bull-leg",
            accepted_source_level=100.0,
            accepted_at_ns=1,
        )
        self.assertEqual(
            gate.observe_counter_acceptance(Direction.LONG),
            None,
        )
        self.assertTrue(gate.is_blocked(Direction.SHORT))
        self.assertEqual(
            gate.observe_counter_acceptance(Direction.SHORT),
            state,
        )
        self.assertFalse(gate.is_blocked(Direction.SHORT))

    def test_new_failure_replaces_same_leg_direction_with_latest_evidence(self) -> None:
        gate = InitiativeAuctionGate()
        gate.accept_failed_reversal(
            blocked_reversal_direction=Direction.SHORT,
            opposing_delivery_price=95.0,
            source_scenario_id="first",
            accepted_source_level=100.0,
            accepted_at_ns=1,
        )
        latest = gate.accept_failed_reversal(
            blocked_reversal_direction=Direction.SHORT,
            opposing_delivery_price=97.0,
            source_scenario_id="latest",
            accepted_source_level=102.0,
            accepted_at_ns=2,
        )
        self.assertEqual(gate.state(Direction.SHORT), latest)
        self.assertEqual(gate.observe_close(97.01), ())
        self.assertEqual(gate.observe_close(97.0), (latest,))


if __name__ == "__main__":
    unittest.main()
