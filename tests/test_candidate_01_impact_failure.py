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
from impact_failure_candidate import ImpactFailureStateMachine
from impact_regime_probe import EventFeature, ScenarioPlan


NS = 1_000_000_000


def volume_bar(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> VolumeBar:
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


def feature(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    imbalance_z: float,
    atr: float = 2.0,
) -> EventFeature:
    return EventFeature(
        bar=volume_bar(
            index,
            open_=open_,
            high=high,
            low=low,
            close=close,
        ),
        true_range=high - low,
        atr=atr,
        imbalance_z=imbalance_z,
    )


def initiative_plan() -> ScenarioPlan:
    return ScenarioPlan(
        scenario_id="impact:0:long:2:continuation",
        response="CONTINUATION",
        side=Side.LONG,
        signal_bar_index=0,
        signal_time_ns=2 * NS,
        stop_price=99.0,
        target_price=120.0,
        confirmation_hold_price=108.0,
        structure_high=108.0,
        structure_low=90.0,
        structure_midpoint=99.0,
        pulse_high=110.0,
        pulse_low=100.0,
        pulse_flow_score=4.0,
        pulse_move_atr=1.0,
        pulse_path_efficiency=0.8,
        pulse_close_location=0.9,
        reason_code="EFFICIENT_IMPACT_OUTSIDE_VALUE",
    )


class ImpactFailureStateMachineTest(unittest.TestCase):
    def test_future_bars_cannot_change_emitted_plan(self) -> None:
        machine = ImpactFailureStateMachine(
            include_intermediate_extremes=True,
            reject_consumed_target=True,
        )
        machine.arm(initiative_plan(), atr=2.0)
        confirmation = feature(
            1,
            open_=108.0,
            high=111.0,
            low=103.0,
            close=104.0,
            imbalance_z=-1.0,
        )
        emitted = machine.on_feature(index=1, feature=confirmation)
        self.assertEqual(len(emitted), 1)
        before = emitted[0]
        self.assertEqual(before.side, Side.SHORT)
        self.assertEqual(before.signal_time_ns, confirmation.bar.end_time_ns)
        self.assertAlmostEqual(before.stop_price, 111.3)

        machine.on_feature(
            index=2,
            feature=feature(
                2,
                open_=104.0,
                high=150.0,
                low=80.0,
                close=140.0,
                imbalance_z=4.0,
            ),
        )
        self.assertEqual(len(machine.plans), 1)
        self.assertEqual(machine.plans[0], before)

    def test_strict_stop_includes_nonconfirming_intermediate_extreme(self) -> None:
        strict = ImpactFailureStateMachine(
            include_intermediate_extremes=True,
            reject_consumed_target=True,
        )
        parity = ImpactFailureStateMachine(
            include_intermediate_extremes=False,
            reject_consumed_target=False,
        )
        for machine in (strict, parity):
            machine.arm(initiative_plan(), atr=2.0)
            machine.on_feature(
                index=1,
                feature=feature(
                    1,
                    open_=109.0,
                    high=112.0,
                    low=107.0,
                    close=109.0,
                    imbalance_z=0.0,
                ),
            )
        confirm = feature(
            2,
            open_=108.0,
            high=109.0,
            low=103.0,
            close=104.0,
            imbalance_z=-1.0,
        )
        strict_plan = strict.on_feature(index=2, feature=confirm)[0]
        parity_plan = parity.on_feature(index=2, feature=confirm)[0]
        self.assertAlmostEqual(strict_plan.stop_price, 112.3)
        self.assertAlmostEqual(parity_plan.stop_price, 110.3)

    def test_consumed_target_invalidates_setup_before_entry(self) -> None:
        machine = ImpactFailureStateMachine(
            include_intermediate_extremes=True,
            reject_consumed_target=True,
        )
        machine.arm(initiative_plan(), atr=2.0)
        emitted = machine.on_feature(
            index=1,
            feature=feature(
                1,
                open_=106.0,
                high=109.0,
                low=89.0,
                close=104.0,
                imbalance_z=-1.0,
            ),
        )
        self.assertEqual(emitted, [])
        self.assertEqual(machine.counts["target_consumed_before_confirmation"], 1)
        self.assertEqual(len(machine.active), 0)
        self.assertEqual(len(machine.plans), 0)

    def test_confirmation_window_expires_without_future_leakage(self) -> None:
        machine = ImpactFailureStateMachine(
            include_intermediate_extremes=True,
            reject_consumed_target=True,
        )
        machine.arm(initiative_plan(), atr=2.0)
        for index in (1, 2, 3):
            emitted = machine.on_feature(
                index=index,
                feature=feature(
                    index,
                    open_=109.0,
                    high=111.0,
                    low=107.0,
                    close=109.0,
                    imbalance_z=0.0,
                ),
            )
            self.assertEqual(emitted, [])
        self.assertEqual(machine.counts["expired"], 1)
        self.assertEqual(len(machine.active), 0)


if __name__ == "__main__":
    unittest.main()
