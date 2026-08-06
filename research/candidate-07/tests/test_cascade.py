from __future__ import annotations

from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from cascade import FailedAbsorptionGate  # noqa: E402
from model import Direction  # noqa: E402


class FailedAbsorptionGateTests(unittest.TestCase):
    def test_long_block_releases_only_after_opposing_liquidity_delivery(self) -> None:
        gate = FailedAbsorptionGate()
        state = gate.block(
            direction=Direction.LONG,
            reset_price=102.5,
            source_scenario_id="long-stop",
            blocked_at_ns=10,
        )
        self.assertTrue(gate.is_blocked(Direction.LONG))
        self.assertFalse(gate.is_blocked(Direction.SHORT))
        self.assertEqual(gate.observe_close(102.49), ())
        self.assertTrue(gate.is_blocked(Direction.LONG))
        self.assertEqual(gate.observe_close(102.5), (state,))
        self.assertFalse(gate.is_blocked(Direction.LONG))

    def test_short_block_is_symmetric(self) -> None:
        gate = FailedAbsorptionGate()
        state = gate.block(
            direction=Direction.SHORT,
            reset_price=97.5,
            source_scenario_id="short-stop",
            blocked_at_ns=20,
        )
        self.assertEqual(gate.observe_close(97.51), ())
        self.assertEqual(gate.observe_close(97.49), (state,))
        self.assertFalse(gate.is_blocked(Direction.SHORT))

    def test_replacing_same_direction_block_keeps_latest_structural_target(self) -> None:
        gate = FailedAbsorptionGate()
        gate.block(
            direction=Direction.LONG,
            reset_price=101.0,
            source_scenario_id="first",
            blocked_at_ns=1,
        )
        latest = gate.block(
            direction=Direction.LONG,
            reset_price=103.0,
            source_scenario_id="second",
            blocked_at_ns=2,
        )
        self.assertEqual(gate.state(Direction.LONG), latest)
        self.assertEqual(gate.observe_close(102.0), ())
        self.assertEqual(gate.observe_close(103.0), (latest,))


if __name__ == "__main__":
    unittest.main()
