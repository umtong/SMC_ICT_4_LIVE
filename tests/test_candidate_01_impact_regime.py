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


def volume_bar(index: int, *, start: int, end: int, open_: float, high: float, low: float, close: float) -> VolumeBar:
    return VolumeBar(
        index=index,
        start_time_ns=start,
        end_time_ns=end,
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


class ImpactEntryBarTest(unittest.TestCase):
    def test_entry_bar_touching_stop_and_target_is_stop_first(self) -> None:
        features = [
            EventFeature(
                bar=volume_bar(
                    0,
                    start=1 * NS,
                    end=2 * NS,
                    open_=100.0,
                    high=102.0,
                    low=99.0,
                    close=101.0,
                ),
                true_range=3.0,
                atr=2.0,
                imbalance_z=2.0,
            ),
            EventFeature(
                bar=volume_bar(
                    1,
                    start=3 * NS,
                    end=4 * NS,
                    open_=101.0,
                    high=111.0,
                    low=89.0,
                    close=105.0,
                ),
                true_range=22.0,
                atr=2.0,
                imbalance_z=1.0,
            ),
        ]
        plan = ScenarioPlan(
            scenario_id="impact-entry-bar-test",
            response="CONTINUATION",
            side=Side.LONG,
            signal_bar_index=0,
            signal_time_ns=2 * NS,
            stop_price=90.0,
            target_price=110.0,
            confirmation_hold_price=100.0,
            structure_high=100.0,
            structure_low=90.0,
            structure_midpoint=95.0,
            pulse_high=102.0,
            pulse_low=99.0,
            pulse_flow_score=3.0,
            pulse_move_atr=1.0,
            pulse_path_efficiency=0.8,
            pulse_close_location=0.8,
            reason_code="TEST",
        )
        trades, metrics, _, _ = simulate(
            features=features,
            plans=[plan],
            evaluation_start_ns=0,
            evaluation_end_ns=10 * NS,
            starting_nav=100_000.0,
            cost=0.0,
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(str(trades.iloc[0]["exit_reason"]), "STOP")
        self.assertEqual(metrics["counters"]["entry_bar_stop_first"], 1)
        self.assertAlmostEqual(float(trades.iloc[0]["realized_r"]), -1.0)


if __name__ == "__main__":
    unittest.main()
