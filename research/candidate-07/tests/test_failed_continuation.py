from __future__ import annotations

from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from failed_continuation import AcceptanceOutcome, FailedAbsorptionAcceptance  # noqa: E402
from model import Direction  # noqa: E402


class FailedAbsorptionAcceptanceTests(unittest.TestCase):
    def test_failed_short_absorption_confirms_long_only_above_stop_boundary(self) -> None:
        state = FailedAbsorptionAcceptance(
            source_scenario_id="short-stop",
            direction=Direction.LONG,
            liquidity_level=100.0,
            acceptance_level=101.0,
            atr=2.0,
            armed_at_ns=10,
            timeout_bars=3,
        )
        first = state.observe(100.6)
        self.assertEqual(first.outcome, AcceptanceOutcome.WAITING)
        second = state.observe(101.1)
        self.assertEqual(second.outcome, AcceptanceOutcome.CONFIRMED)
        self.assertEqual(second.bars_seen, 2)

    def test_failed_short_absorption_is_reclaimed_inside_original_pool(self) -> None:
        state = FailedAbsorptionAcceptance(
            source_scenario_id="short-stop",
            direction=Direction.LONG,
            liquidity_level=100.0,
            acceptance_level=101.0,
            atr=2.0,
            armed_at_ns=10,
        )
        result = state.observe(99.9)
        self.assertEqual(result.outcome, AcceptanceOutcome.RECLAIMED)

    def test_failed_long_absorption_confirms_short_symmetrically(self) -> None:
        state = FailedAbsorptionAcceptance(
            source_scenario_id="long-stop",
            direction=Direction.SHORT,
            liquidity_level=100.0,
            acceptance_level=99.0,
            atr=2.0,
            armed_at_ns=10,
        )
        result = state.observe(98.9)
        self.assertEqual(result.outcome, AcceptanceOutcome.CONFIRMED)
        self.assertEqual(result.reason_code, "FAILED_LONG_ABSORPTION_ACCEPTED_LOWER")

    def test_ambiguous_acceptance_times_out_without_trade(self) -> None:
        state = FailedAbsorptionAcceptance(
            source_scenario_id="short-stop",
            direction=Direction.LONG,
            liquidity_level=100.0,
            acceptance_level=101.0,
            atr=2.0,
            armed_at_ns=10,
            timeout_bars=2,
        )
        self.assertEqual(state.observe(100.4).outcome, AcceptanceOutcome.WAITING)
        self.assertEqual(state.observe(100.5).outcome, AcceptanceOutcome.TIMED_OUT)

    def test_invalid_boundary_orientation_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FailedAbsorptionAcceptance(
                source_scenario_id="bad",
                direction=Direction.LONG,
                liquidity_level=100.0,
                acceptance_level=99.0,
                atr=2.0,
                armed_at_ns=10,
            )


if __name__ == "__main__":
    unittest.main()
