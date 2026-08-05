from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "research" / "candidate-01"
SRC = ROOT / "src"
for item in (CANDIDATE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from internal_liquidity_transfer_week import classify_transfer
from impact_regime_probe import PulseEvent


NS = 1_000_000_000


def pulse(
    *,
    direction: str = "LONG",
    pulse_high: float = 99.0,
    pulse_low: float = 93.0,
    pulse_close: float = 98.0,
    move_atr: float = 1.0,
    efficiency: float = 0.8,
    close_location: float = 0.85,
) -> PulseEvent:
    return PulseEvent(
        scenario_id="internal-transfer-test",
        bar_index=10,
        event_time_ns=20 * NS,
        direction=direction,
        flow_score=3.5 if direction == "LONG" else -3.5,
        previous_flow_score=1.0,
        same_direction_bars=3,
        atr=2.0,
        structure_high=100.0,
        structure_low=90.0,
        structure_width_atr=5.0,
        pulse_high=pulse_high,
        pulse_low=pulse_low,
        pulse_close=pulse_close,
        move_atr=move_atr,
        path_efficiency=efficiency,
        aligned_close_location=close_location,
        outward_excursion_atr=0.0,
        close_beyond_boundary_atr=-1.0,
        classification="NO_TRADE",
        reason="test",
    )


class InternalLiquidityTransferTest(unittest.TestCase):
    def test_discount_to_premium_transfer_emits_long_plan(self) -> None:
        decision, plan = classify_transfer(
            pulse=pulse(),
            start_price=93.0,
        )
        self.assertTrue(decision.accepted)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.side.value, "LONG")
        self.assertEqual(plan.confirmation_hold_price, 95.0)
        self.assertEqual(plan.target_price, 100.0)
        self.assertLess(plan.stop_price, 93.0)

    def test_target_touched_during_pulse_rejects_plan(self) -> None:
        decision, plan = classify_transfer(
            pulse=pulse(pulse_high=100.0),
            start_price=93.0,
        )
        self.assertFalse(decision.accepted)
        self.assertIsNone(plan)
        self.assertEqual(decision.reason_code, "LONG_EXTERNAL_TARGET_ALREADY_TOUCHED")

    def test_long_starting_in_premium_is_not_transfer(self) -> None:
        decision, plan = classify_transfer(
            pulse=pulse(),
            start_price=97.0,
        )
        self.assertFalse(decision.accepted)
        self.assertIsNone(plan)
        self.assertEqual(decision.reason_code, "LONG_DID_NOT_BEGIN_IN_DISCOUNT")

    def test_short_premium_to_discount_is_symmetric(self) -> None:
        decision, plan = classify_transfer(
            pulse=pulse(
                direction="SHORT",
                pulse_high=97.0,
                pulse_low=91.0,
                pulse_close=92.0,
            ),
            start_price=97.0,
        )
        self.assertTrue(decision.accepted)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.side.value, "SHORT")
        self.assertEqual(plan.target_price, 90.0)
        self.assertGreater(plan.stop_price, 97.0)


if __name__ == "__main__":
    unittest.main()
