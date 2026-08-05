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
from impact_regime_probe import EventFeature
from rolling_range_sweep_week import RANGE_BARS, RollingRangeSweepStateMachine


NS = 1_000_000_000


def feature(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    atr: float = 2.0,
    z: float | None = 0.0,
) -> EventFeature:
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
        target_quote_notional=0.0,
    )
    return EventFeature(bar=bar, true_range=high-low, atr=atr, imbalance_z=z)


def range_history() -> list[EventFeature]:
    result=[]
    for i in range(RANGE_BARS):
        high=100.0 if i==3 else 99.0
        low=90.0 if i==7 else 91.0
        result.append(feature(i,open_=95.0,high=high,low=low,close=95.0,z=0.0))
    return result


class RollingRangeSweepTest(unittest.TestCase):
    def test_high_sweep_then_opposite_flow_emits_short_rotation(self) -> None:
        machine=RollingRangeSweepStateMachine()
        history=range_history()
        sweep=feature(RANGE_BARS,open_=99.5,high=101.0,low=98.0,close=99.0,z=1.0)
        emitted=machine.on_feature(index=RANGE_BARS,feature=sweep,prior_features=history)
        self.assertEqual(emitted,[])
        self.assertEqual(machine.counts["armed"],1)
        confirm=feature(RANGE_BARS+1,open_=99.0,high=99.2,low=97.0,close=98.0,z=-1.0)
        emitted=machine.on_feature(index=RANGE_BARS+1,feature=confirm,prior_features=[*history,sweep])
        self.assertEqual(len(emitted),1)
        plan=emitted[0]
        self.assertEqual(plan.side.value,"SHORT")
        self.assertEqual(plan.target_price,95.0)
        self.assertGreater(plan.stop_price,101.0)
        self.assertEqual(plan.confirmation_hold_price,100.0)

    def test_equilibrium_touched_before_confirmation_cancels(self) -> None:
        machine=RollingRangeSweepStateMachine()
        history=range_history()
        sweep=feature(RANGE_BARS,open_=99.5,high=101.0,low=98.0,close=99.0,z=1.0)
        machine.on_feature(index=RANGE_BARS,feature=sweep,prior_features=history)
        confirm=feature(RANGE_BARS+1,open_=99.0,high=99.2,low=94.5,close=98.0,z=-1.0)
        emitted=machine.on_feature(index=RANGE_BARS+1,feature=confirm,prior_features=[*history,sweep])
        self.assertEqual(emitted,[])
        self.assertEqual(machine.counts["target_consumed"],1)
        self.assertIsNone(machine.active)

    def test_future_bar_cannot_modify_emitted_plan(self) -> None:
        machine=RollingRangeSweepStateMachine()
        history=range_history()
        sweep=feature(RANGE_BARS,open_=99.5,high=101.0,low=98.0,close=99.0,z=1.0)
        machine.on_feature(index=RANGE_BARS,feature=sweep,prior_features=history)
        confirm=feature(RANGE_BARS+1,open_=99.0,high=99.2,low=97.0,close=98.0,z=-1.0)
        plan=machine.on_feature(index=RANGE_BARS+1,feature=confirm,prior_features=[*history,sweep])[0]
        signature=(plan.signal_time_ns,plan.stop_price,plan.target_price)
        future=feature(RANGE_BARS+2,open_=98.0,high=120.0,low=80.0,close=110.0,z=3.0)
        machine.on_feature(index=RANGE_BARS+2,feature=future,prior_features=[*history,sweep,confirm])
        self.assertEqual(signature,(machine.plans[0].signal_time_ns,machine.plans[0].stop_price,machine.plans[0].target_price))


if __name__ == "__main__":
    unittest.main()
