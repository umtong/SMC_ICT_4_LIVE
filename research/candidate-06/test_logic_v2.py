from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from logic import BarObservation, LiquidityResponseScenarioEngine, PrimitiveSnapshot


def snapshot(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    flow: float,
    upper_fast: float = 101.0,
    lower_fast: float = 90.0,
    upper_slow: float = 110.0,
    lower_slow: float = 80.0,
) -> PrimitiveSnapshot:
    candle_range = high - low
    body = abs(close - open_)
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(
            ts_ns=index * 60_000_000_000,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1000.0,
            taker_buy_volume=1000.0 * (flow + 1.0) / 2.0,
            trades=100,
        ),
        ready=True,
        atr=1.0,
        rel_volume=2.0,
        flow_ratio=flow,
        body_atr=body,
        range_atr=candle_range,
        upper_wick_fraction=max(high - max(open_, close), 0.0) / candle_range,
        lower_wick_fraction=max(min(open_, close) - low, 0.0) / candle_range,
        close_location=(close - low) / candle_range,
        upper_fast=upper_fast,
        lower_fast=lower_fast,
        upper_slow=upper_slow,
        lower_slow=lower_slow,
        slow_mid=(upper_slow + lower_slow) / 2.0,
        range_position=(close - lower_slow) / (upper_slow - lower_slow),
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


class PostSweepResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.params = json.loads((HERE / "config.json").read_text(encoding="utf-8"))["logic"]
        self.params["response_observation_bars"] = 3
        self.params["minimum_structural_rr"] = min(float(self.params["minimum_structural_rr"]), 1.2)

    def test_sweep_bar_cannot_emit_entry_signal(self) -> None:
        engine = LiquidityResponseScenarioEngine(self.params)
        sweep = snapshot(100, open_=100.0, high=105.0, low=99.0, close=102.0, flow=0.9)
        first = engine.observe(sweep, allow_new=True)
        self.assertIsNone(first.signal)
        self.assertEqual(first.transitions[0].previous_state, "IDLE")
        self.assertEqual(first.transitions[0].next_state, "UPPER_SWEEP_RESPONSE_OBSERVATION")

        response = snapshot(101, open_=102.0, high=102.2, low=96.0, close=97.0, flow=-0.8)
        second = engine.observe(response, allow_new=True)
        self.assertIsNotNone(second.signal)
        assert second.signal is not None
        self.assertEqual(second.signal.family, "SRR")
        self.assertEqual(second.signal.direction, "SHORT")
        self.assertGreaterEqual(len(second.transitions), 2)
        self.assertEqual(second.transitions[0].previous_state, "UPPER_SWEEP_RESPONSE_OBSERVATION")
        self.assertEqual(second.transitions[0].next_state, "UPPER_SRR_RESPONSE_CONFIRMED")
        self.assertEqual(second.transitions[-1].next_state, "ENTRY_ARMED")

    def test_unidentified_response_abstains_after_fixed_window(self) -> None:
        engine = LiquidityResponseScenarioEngine(self.params)
        first = engine.observe(snapshot(200, open_=100.0, high=105.0, low=99.0, close=102.0, flow=0.9), allow_new=True)
        self.assertIsNone(first.signal)
        last = None
        for index in range(201, 205):
            last = engine.observe(snapshot(index, open_=101.0, high=101.4, low=100.6, close=101.0, flow=0.0), allow_new=True)
        assert last is not None
        self.assertIsNone(last.signal)
        states = [transition.next_state for transition in last.transitions]
        self.assertEqual(states, ["AMBIGUOUS", "RESET"])


if __name__ == "__main__":
    unittest.main()
