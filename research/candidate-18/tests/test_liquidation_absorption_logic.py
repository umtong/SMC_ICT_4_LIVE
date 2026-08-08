from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from liquidation_absorption_logic import LiquidationEvent
from liquidation_absorption_logic import MinuteObservation
from liquidation_absorption_logic import evaluate_confirmation


class LiquidationAbsorptionLogicTest(unittest.TestCase):
    def event(self, direction: int = -1) -> LiquidationEvent:
        if direction < 0:
            return LiquidationEvent(-1, 100.0, 101.0, 95.0, 96.0, 112.0, 115.0, 2.0, 2.0, 1.5)
        return LiquidationEvent(1, 100.0, 105.0, 99.0, 104.0, 88.0, 85.0, 2.0, 2.0, 1.5)

    def bar(self, direction: int = -1, close: float | None = None) -> MinuteObservation:
        if direction < 0:
            return MinuteObservation(101.0, 96.0, 100.5 if close is None else close, 0.20, 0.7, 0.5)
        return MinuteObservation(104.0, 98.0, 99.5 if close is None else close, -0.20, 0.7, 0.5)

    def test_open_reclaim_long_and_short_are_symmetric(self) -> None:
        long = evaluate_confirmation("OPEN_RECLAIM", self.event(-1), [self.bar(-1)])
        short = evaluate_confirmation("OPEN_RECLAIM", self.event(1), [self.bar(1)])
        self.assertTrue(long.confirmed)
        self.assertTrue(short.confirmed)
        self.assertLess(long.stop, long.entry)
        self.assertGreater(long.target, long.entry)
        self.assertGreater(short.stop, short.entry)
        self.assertLess(short.target, short.entry)

    def test_basis_must_compress_after_event(self) -> None:
        bar = MinuteObservation(101.0, 96.0, 100.5, 0.20, 1.5, 1.2)
        decision = evaluate_confirmation("OPEN_RECLAIM", self.event(-1), [bar])
        self.assertFalse(decision.confirmed)
        self.assertEqual(decision.reason, "DERIVATIVE_BASIS_NOT_COMPRESSED")

    def test_two_minute_absorption_rejects_new_extreme(self) -> None:
        first = MinuteObservation(98.0, 94.0, 96.0, 0.10, 0.7, 0.5)
        second = MinuteObservation(100.0, 95.5, 99.0, 0.15, 0.6, 0.4)
        decision = evaluate_confirmation("TWO_MINUTE_ABSORPTION", self.event(-1), [first, second])
        self.assertFalse(decision.confirmed)
        self.assertEqual(decision.reason, "LIQUIDATION_EXTREME_STILL_EXPANDING")

    def test_structural_target_must_offer_one_r(self) -> None:
        event = LiquidationEvent(-1, 100.0, 101.0, 95.0, 96.0, 101.0, 101.2, 2.0, 2.0, 1.5)
        decision = evaluate_confirmation("OPEN_RECLAIM", event, [self.bar(-1)])
        self.assertFalse(decision.confirmed)
        self.assertEqual(decision.reason, "STRUCTURAL_TARGET_BELOW_ONE_R")

    def test_no_same_minute_confirmation(self) -> None:
        decision = evaluate_confirmation("OPEN_RECLAIM", self.event(-1), [])
        self.assertFalse(decision.confirmed)
        self.assertEqual(decision.reason, "NO_LATER_OBSERVATION")


if __name__ == "__main__":
    unittest.main()
