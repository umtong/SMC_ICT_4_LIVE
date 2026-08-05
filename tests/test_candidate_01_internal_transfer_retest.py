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
from impact_regime_probe import EventFeature, PulseEvent, ScenarioPlan
from internal_transfer_retest_week import TransferRetestStateMachine


NS = 1_000_000_000


def feature(index: int, *, high: float, low: float, close: float, imbalance: float) -> EventFeature:
    quote = 100.0
    signed = quote * imbalance
    bar = VolumeBar(
        index=index,
        start_time_ns=(2 * index + 1) * NS,
        end_time_ns=(2 * index + 2) * NS,
        open=close,
        high=high,
        low=low,
        close=close,
        base_quantity=1.0,
        quote_notional=quote,
        signed_quote_notional=signed,
        aggressive_buy_quote=max(signed, 0.0),
        aggressive_sell_quote=max(-signed, 0.0),
        aggregate_trades=1,
        first_agg_trade_id=index,
        last_agg_trade_id=index,
        target_quote_notional=0.0,
    )
    return EventFeature(bar=bar, true_range=high-low, atr=None, imbalance_z=None)


def source_plan() -> ScenarioPlan:
    return ScenarioPlan(
        scenario_id="transfer-source",
        response="CONTINUATION",
        side=Side.LONG,
        signal_bar_index=0,
        signal_time_ns=2 * NS,
        stop_price=91.0,
        target_price=100.0,
        confirmation_hold_price=95.0,
        structure_high=100.0,
        structure_low=90.0,
        structure_midpoint=95.0,
        pulse_high=99.0,
        pulse_low=93.0,
        pulse_flow_score=3.5,
        pulse_move_atr=1.2,
        pulse_path_efficiency=0.8,
        pulse_close_location=0.9,
        reason_code="INTERNAL_LIQUIDITY_TRANSFER_CONFIRMED",
    )


def pulse() -> PulseEvent:
    return PulseEvent(
        scenario_id="transfer-source",
        bar_index=0,
        event_time_ns=2 * NS,
        direction="LONG",
        flow_score=3.5,
        previous_flow_score=1.0,
        same_direction_bars=3,
        atr=2.0,
        structure_high=100.0,
        structure_low=90.0,
        structure_width_atr=5.0,
        pulse_high=99.0,
        pulse_low=93.0,
        pulse_close=98.0,
        move_atr=1.2,
        path_efficiency=0.8,
        aligned_close_location=0.9,
        outward_excursion_atr=0.0,
        close_beyond_boundary_atr=-1.0,
        classification="NO_TRADE",
        reason="test",
    )


class InternalTransferRetestTest(unittest.TestCase):
    def test_equilibrium_retest_with_aligned_flow_emits_plan(self) -> None:
        machine = TransferRetestStateMachine()
        machine.arm(source=source_plan(), pulse=pulse())
        observed = feature(1, high=96.0, low=95.1, close=95.5, imbalance=0.20)
        emitted = machine.on_feature(index=1, feature=observed)
        self.assertEqual(len(emitted), 1)
        plan = emitted[0]
        self.assertEqual(plan.side, Side.LONG)
        self.assertEqual(plan.target_price, 100.0)
        self.assertLess(plan.stop_price, 93.0)
        self.assertEqual(plan.confirmation_hold_price, 95.0)

    def test_wrong_flow_does_not_confirm_retest(self) -> None:
        machine = TransferRetestStateMachine()
        machine.arm(source=source_plan(), pulse=pulse())
        observed = feature(1, high=96.0, low=95.1, close=95.5, imbalance=-0.20)
        emitted = machine.on_feature(index=1, feature=observed)
        self.assertEqual(emitted, [])
        self.assertEqual(len(machine.active), 1)

    def test_equilibrium_failure_cancels_setup(self) -> None:
        machine = TransferRetestStateMachine()
        machine.arm(source=source_plan(), pulse=pulse())
        observed = feature(1, high=95.0, low=94.0, close=94.5, imbalance=0.20)
        emitted = machine.on_feature(index=1, feature=observed)
        self.assertEqual(emitted, [])
        self.assertEqual(machine.counts["equilibrium_failed"], 1)
        self.assertEqual(machine.active, [])

    def test_target_before_entry_cancels_setup(self) -> None:
        machine = TransferRetestStateMachine()
        machine.arm(source=source_plan(), pulse=pulse())
        observed = feature(1, high=100.1, low=96.0, close=99.0, imbalance=0.20)
        emitted = machine.on_feature(index=1, feature=observed)
        self.assertEqual(emitted, [])
        self.assertEqual(machine.counts["target_consumed"], 1)


if __name__ == "__main__":
    unittest.main()
