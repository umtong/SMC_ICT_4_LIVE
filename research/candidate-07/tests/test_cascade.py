from __future__ import annotations

from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from cascade import FailedAbsorptionGate  # noqa: E402
from model import Direction  # noqa: E402


class FailedAbsorptionGateTests(unittest.TestCase):
    def test_long_source_releases_only_after_opposing_liquidity_delivery(self) -> None:
        gate = FailedAbsorptionGate()
        state = gate.block(
            direction=Direction.LONG,
            source_liquidity_level=98.0,
            reset_price=102.5,
            source_scenario_id="long-stop",
            blocked_at_ns=10,
        )
        self.assertTrue(gate.is_blocked(Direction.LONG, 98.0))
        self.assertFalse(gate.is_blocked(Direction.LONG, 97.0))
        self.assertFalse(gate.is_blocked(Direction.SHORT, 98.0))
        self.assertEqual(gate.observe_close(102.49), ())
        self.assertTrue(gate.is_blocked(Direction.LONG, 98.0))
        self.assertEqual(gate.observe_close(102.5), (state,))
        self.assertFalse(gate.is_blocked(Direction.LONG, 98.0))

    def test_short_source_is_symmetric(self) -> None:
        gate = FailedAbsorptionGate()
        state = gate.block(
            direction=Direction.SHORT,
            source_liquidity_level=102.0,
            reset_price=97.5,
            source_scenario_id="short-stop",
            blocked_at_ns=20,
        )
        self.assertEqual(gate.observe_close(97.51), ())
        self.assertEqual(gate.observe_close(97.49), (state,))
        self.assertFalse(gate.is_blocked(Direction.SHORT, 102.0))

    def test_independent_same_direction_sources_coexist(self) -> None:
        gate = FailedAbsorptionGate()
        first = gate.block(
            direction=Direction.LONG,
            source_liquidity_level=98.0,
            reset_price=101.0,
            source_scenario_id="first-source",
            blocked_at_ns=1,
        )
        second = gate.block(
            direction=Direction.LONG,
            source_liquidity_level=96.0,
            reset_price=103.0,
            source_scenario_id="second-source",
            blocked_at_ns=2,
        )
        self.assertEqual(gate.state(Direction.LONG, 98.0), first)
        self.assertEqual(gate.state(Direction.LONG, 96.0), second)
        self.assertFalse(gate.is_blocked(Direction.LONG, 94.0))
        self.assertEqual(gate.observe_close(101.0), (first,))
        self.assertFalse(gate.is_blocked(Direction.LONG, 98.0))
        self.assertTrue(gate.is_blocked(Direction.LONG, 96.0))
        self.assertEqual(gate.observe_close(103.0), (second,))

    def test_repeating_same_source_replaces_only_that_source(self) -> None:
        gate = FailedAbsorptionGate()
        gate.block(
            direction=Direction.LONG,
            source_liquidity_level=98.0,
            reset_price=101.0,
            source_scenario_id="first",
            blocked_at_ns=1,
        )
        latest = gate.block(
            direction=Direction.LONG,
            source_liquidity_level=98.0,
            reset_price=103.0,
            source_scenario_id="second",
            blocked_at_ns=2,
        )
        self.assertEqual(gate.state(Direction.LONG, 98.0), latest)
        self.assertEqual(gate.observe_close(102.0), ())
        self.assertEqual(gate.observe_close(103.0), (latest,))

    def test_legacy_direction_owner_remains_backward_compatible(self) -> None:
        gate = FailedAbsorptionGate()
        latest = gate.block(
            direction=Direction.LONG,
            reset_price=103.0,
            source_scenario_id="legacy",
            blocked_at_ns=2,
        )
        self.assertEqual(gate.state(Direction.LONG), latest)
        self.assertTrue(gate.is_blocked(Direction.LONG))
        self.assertEqual(gate.observe_close(103.0), (latest,))


if __name__ == "__main__":
    unittest.main()
