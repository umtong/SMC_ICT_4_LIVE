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

from absorbed_impact_release_week import AbsorbedImpactStateMachine, classify_absorption
from aggtrade_clock import VolumeBar
from impact_regime_probe import EventFeature, PulseEvent


NS = 1_000_000_000


def feature(index: int, *, open_: float, high: float, low: float, close: float, z: float) -> EventFeature:
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
    return EventFeature(bar=bar, true_range=high-low, atr=2.0, imbalance_z=z)


def absorbed_pulse() -> PulseEvent:
    return PulseEvent(
        scenario_id="absorbed-pulse",
        bar_index=0,
        event_time_ns=2 * NS,
        direction="LONG",
        flow_score=3.5,
        previous_flow_score=2.0,
        same_direction_bars=3,
        atr=2.0,
        structure_high=100.0,
        structure_low=90.0,
        structure_width_atr=5.0,
        pulse_high=102.0,
        pulse_low=96.0,
        pulse_close=99.5,
        move_atr=0.8,
        path_efficiency=0.5,
        aligned_close_location=0.25,
        outward_excursion_atr=1.0,
        close_beyond_boundary_atr=-0.25,
        classification="NO_TRADE",
        reason="test",
    )


class AbsorbedImpactTest(unittest.TestCase):
    def test_absorbed_pulse_classification(self) -> None:
        accepted, reason, retention = classify_absorption(absorbed_pulse())
        self.assertTrue(accepted)
        self.assertEqual(reason, "ABSORBED_EXTERNAL_IMPACT")
        self.assertAlmostEqual(float(retention), -0.25)

    def test_opposite_release_emits_after_later_completed_event(self) -> None:
        machine = AbsorbedImpactStateMachine()
        pulse_feature = feature(0, open_=99.0, high=102.0, low=96.0, close=99.5, z=2.0)
        machine.observe_pulse(pulse=absorbed_pulse(), feature=pulse_feature)
        self.assertEqual(machine.plans, [])
        emitted = machine.on_feature(
            index=1,
            feature=feature(1, open_=99.5, high=100.0, low=96.5, close=97.5, z=-1.0),
        )
        self.assertEqual(len(emitted), 1)
        plan = emitted[0]
        self.assertEqual(plan.side.value, "SHORT")
        self.assertGreater(plan.stop_price, 102.0)
        self.assertEqual(plan.target_price, 90.0)
        self.assertEqual(machine.counts["confirmed"], 1)

    def test_future_bars_do_not_modify_emitted_plan(self) -> None:
        machine = AbsorbedImpactStateMachine()
        pulse_feature = feature(0, open_=99.0, high=102.0, low=96.0, close=99.5, z=2.0)
        machine.observe_pulse(pulse=absorbed_pulse(), feature=pulse_feature)
        emitted = machine.on_feature(
            index=1,
            feature=feature(1, open_=99.5, high=100.0, low=96.5, close=97.5, z=-1.0),
        )
        signature = (
            emitted[0].stop_price,
            emitted[0].target_price,
            emitted[0].signal_time_ns,
        )
        machine.on_feature(
            index=2,
            feature=feature(2, open_=97.5, high=110.0, low=80.0, close=105.0, z=3.0),
        )
        self.assertEqual(
            signature,
            (
                machine.plans[0].stop_price,
                machine.plans[0].target_price,
                machine.plans[0].signal_time_ns,
            ),
        )


if __name__ == "__main__":
    unittest.main()
