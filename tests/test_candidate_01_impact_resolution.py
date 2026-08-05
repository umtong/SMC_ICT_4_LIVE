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
from impact_resolution_candidate import ImpactResolutionStateMachine


NS = 1_000_000_000


def volume_bar(index: int, *, open_: float, high: float, low: float, close: float) -> VolumeBar:
    return VolumeBar(
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


def feature(index: int, *, open_: float, high: float, low: float, close: float, z: float) -> EventFeature:
    return EventFeature(
        bar=volume_bar(index, open_=open_, high=high, low=low, close=close),
        true_range=high - low,
        atr=2.0,
        imbalance_z=z,
    )


def initiative() -> ScenarioPlan:
    return ScenarioPlan(
        scenario_id="resolution-test",
        response="CONTINUATION",
        side=Side.LONG,
        signal_bar_index=0,
        signal_time_ns=2 * NS,
        stop_price=97.0,
        target_price=110.0,
        confirmation_hold_price=100.0,
        structure_high=100.0,
        structure_low=90.0,
        structure_midpoint=95.0,
        pulse_high=104.0,
        pulse_low=98.0,
        pulse_flow_score=4.0,
        pulse_move_atr=2.0,
        pulse_path_efficiency=0.8,
        pulse_close_location=0.9,
        reason_code="TEST_INITIATIVE",
    )


class ImpactResolutionTest(unittest.TestCase):
    def test_failure_has_precedence_over_early_persistence(self) -> None:
        machine = ImpactResolutionStateMachine()
        first = feature(0, open_=103.0, high=104.0, low=102.0, close=103.5, z=2.0)
        machine.on_feature(index=0, feature=first, new_initiative_plans=[initiative()])
        machine.on_feature(index=1, feature=feature(1, open_=103.5, high=105.0, low=102.0, close=104.0, z=0.8))
        machine.on_feature(index=2, feature=feature(2, open_=104.0, high=104.5, low=101.0, close=102.5, z=0.6))
        emitted = machine.on_feature(
            index=3,
            feature=feature(3, open_=102.5, high=103.0, low=98.0, close=99.0, z=-1.0),
        )
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].side, Side.SHORT)
        self.assertEqual(emitted[0].response, "EXHAUSTION_REVERSAL")
        self.assertEqual(machine.counts["resolved_reversal"], 1)
        self.assertEqual(machine.counts["resolved_continuation"], 0)
        self.assertGreater(emitted[0].stop_price, 105.0)

    def test_continuation_waits_for_full_resolution_window(self) -> None:
        machine = ImpactResolutionStateMachine()
        first = feature(0, open_=103.0, high=104.0, low=102.0, close=103.5, z=2.0)
        machine.on_feature(index=0, feature=first, new_initiative_plans=[initiative()])
        self.assertEqual(machine.on_feature(index=1, feature=feature(1, open_=103.5, high=105.0, low=102.0, close=104.0, z=0.3)), [])
        self.assertEqual(machine.on_feature(index=2, feature=feature(2, open_=104.0, high=105.5, low=102.5, close=104.5, z=0.3)), [])
        emitted = machine.on_feature(
            index=3,
            feature=feature(3, open_=104.5, high=106.0, low=103.0, close=105.0, z=0.3),
        )
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].side, Side.LONG)
        self.assertEqual(emitted[0].response, "CONTINUATION")
        self.assertEqual(machine.counts["resolved_continuation"], 1)
        self.assertLess(emitted[0].stop_price, 98.0)


if __name__ == "__main__":
    unittest.main()
