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
from impact_regime_probe import EventFeature, ScenarioPlan, simulate


NS = 1_000_000_000


def feature(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
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
        target_quote_notional=100.0,
    )
    return EventFeature(
        bar=bar,
        true_range=high - low,
        atr=5.0,
        imbalance_z=0.0,
    )


def short_plan() -> ScenarioPlan:
    return ScenarioPlan(
        scenario_id="reacceptance-test",
        response="EXHAUSTION_REVERSAL",
        side=Side.SHORT,
        signal_bar_index=0,
        signal_time_ns=2 * NS,
        stop_price=120.0,
        target_price=50.0,
        confirmation_hold_price=100.0,
        structure_high=100.0,
        structure_low=50.0,
        structure_midpoint=75.0,
        pulse_high=115.0,
        pulse_low=95.0,
        pulse_flow_score=4.0,
        pulse_move_atr=1.0,
        pulse_path_efficiency=0.8,
        pulse_close_location=0.2,
        reason_code="TEST",
    )


class BoundaryReacceptanceExitTest(unittest.TestCase):
    def test_completed_reacceptance_exits_at_next_event_open(self) -> None:
        features = [
            feature(0, open_=99.0, high=101.0, low=98.0, close=99.0),
            feature(1, open_=95.0, high=105.0, low=90.0, close=101.0),
            feature(2, open_=102.0, high=103.0, low=99.0, close=100.0),
        ]
        trades, metrics, _, _ = simulate(
            features=features,
            plans=[short_plan()],
            evaluation_start_ns=0,
            evaluation_end_ns=10 * NS,
            starting_nav=100_000.0,
            cost=0.0007,
            exit_on_boundary_reacceptance=True,
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(str(trades.iloc[0]["exit_reason"]), "BOUNDARY_REACCEPTANCE")
        self.assertEqual(int(trades.iloc[0]["exit_time_ns"]), features[2].bar.start_time_ns)
        self.assertAlmostEqual(float(trades.iloc[0]["exit_price"]), 102.0)
        self.assertEqual(metrics["counters"]["boundary_reacceptance_signals"], 1)
        self.assertEqual(metrics["counters"]["boundary_reacceptance_exits"], 1)

    def test_live_stop_has_precedence_when_next_open_gaps_through_stop(self) -> None:
        features = [
            feature(0, open_=99.0, high=101.0, low=98.0, close=99.0),
            feature(1, open_=95.0, high=105.0, low=90.0, close=101.0),
            feature(2, open_=125.0, high=126.0, low=124.0, close=125.0),
        ]
        trades, metrics, _, _ = simulate(
            features=features,
            plans=[short_plan()],
            evaluation_start_ns=0,
            evaluation_end_ns=10 * NS,
            starting_nav=100_000.0,
            cost=0.0007,
            exit_on_boundary_reacceptance=True,
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(str(trades.iloc[0]["exit_reason"]), "STOP")
        self.assertAlmostEqual(float(trades.iloc[0]["exit_price"]), 125.0)
        self.assertEqual(metrics["counters"]["boundary_reacceptance_stop_gaps"], 1)


if __name__ == "__main__":
    unittest.main()
