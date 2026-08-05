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

from aggtrade_clock import VolumeBar
from core import Side
from impact_regime_probe import EventFeature, ScenarioPlan
from impact_resolution_retest_week import AcceptanceRetestStateMachine


NS = 1_000_000_000


def feature(index: int, *, open_: float, high: float, low: float, close: float) -> EventFeature:
    bar = VolumeBar(
        index=index,
        start_time_ns=(2 * index + 1) * NS,
        end_time_ns=(2 * index + 2) * NS,
        open=open_,
        high=high,
        low=low,
        close=close,
        base_quantity=1.0,
        quote_notional=100.0,
        signed_quote_notional=0.0,
        aggressive_buy_quote=50.0,
        aggressive_sell_quote=50.0,
        aggregate_trades=1,
        first_agg_trade_id=index,
        last_agg_trade_id=index,
        target_quote_notional=100.0,
    )
    return EventFeature(bar=bar, true_range=high-low, atr=2.0, imbalance_z=0.0)


def continuation() -> ScenarioPlan:
    return ScenarioPlan(
        scenario_id="accepted-impact",
        response="CONTINUATION",
        side=Side.LONG,
        signal_bar_index=0,
        signal_time_ns=2 * NS,
        stop_price=96.0,
        target_price=110.0,
        confirmation_hold_price=100.0,
        structure_high=100.0,
        structure_low=90.0,
        structure_midpoint=95.0,
        pulse_high=105.0,
        pulse_low=97.0,
        pulse_flow_score=4.0,
        pulse_move_atr=2.0,
        pulse_path_efficiency=0.8,
        pulse_close_location=0.9,
        reason_code="OUTSIDE_IMPACT_DURABLY_ACCEPTED",
    )


class AcceptanceRetestTest(unittest.TestCase):
    def test_boundary_touch_and_outside_close_emits_next_event_plan(self) -> None:
        machine = AcceptanceRetestStateMachine()
        armed_feature = feature(0, open_=104.0, high=105.0, low=103.0, close=104.0)
        machine.arm(continuation(), atr=2.0, feature=armed_feature, index=0)
        emitted = machine.on_feature(
            index=1,
            feature=feature(1, open_=103.0, high=104.0, low=99.9, close=100.2),
        )
        self.assertEqual(len(emitted), 1)
        plan = emitted[0]
        self.assertEqual(plan.side, Side.LONG)
        self.assertEqual(plan.confirmation_hold_price, 100.0)
        self.assertLess(plan.stop_price, 100.0)
        self.assertEqual(plan.target_price, 110.0)
        self.assertEqual(machine.counts["confirmed"], 1)

    def test_close_through_boundary_invalidates_before_later_retest(self) -> None:
        machine = AcceptanceRetestStateMachine()
        armed_feature = feature(0, open_=104.0, high=105.0, low=103.0, close=104.0)
        machine.arm(continuation(), atr=2.0, feature=armed_feature, index=0)
        emitted = machine.on_feature(
            index=1,
            feature=feature(1, open_=102.0, high=102.5, low=98.0, close=99.0),
        )
        self.assertEqual(emitted, [])
        self.assertEqual(machine.counts["boundary_failed"], 1)
        self.assertEqual(machine.active, [])

    def test_target_before_retest_cancels_setup(self) -> None:
        machine = AcceptanceRetestStateMachine()
        armed_feature = feature(0, open_=104.0, high=105.0, low=103.0, close=104.0)
        machine.arm(continuation(), atr=2.0, feature=armed_feature, index=0)
        emitted = machine.on_feature(
            index=1,
            feature=feature(1, open_=105.0, high=111.0, low=104.0, close=109.0),
        )
        self.assertEqual(emitted, [])
        self.assertEqual(machine.counts["target_consumed"], 1)


if __name__ == "__main__":
    unittest.main()
