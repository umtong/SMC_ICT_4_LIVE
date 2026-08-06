from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from lrb_types import BarObservation, PrimitiveSnapshot
from session_engine import SessionLiquidityTransferEngine


def ns(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def snap(index: int, timestamp: int, open_: float, high: float, low: float, close: float, flow: float) -> PrimitiveSnapshot:
    width = high - low
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(
            ts_ns=timestamp,
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
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=max(high - max(open_, close), 0.0) / width,
        lower_wick_fraction=max(min(open_, close) - low, 0.0) / width,
        close_location=(close - low) / width,
        upper_fast=101.0,
        lower_fast=95.0,
        upper_slow=105.0,
        lower_slow=90.0,
        slow_mid=97.5,
        range_position=(close - 90.0) / 15.0,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


class SessionLiquidityTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        params = json.loads((HERE / "config.json").read_text(encoding="utf-8"))["logic"]
        params.update(
            {
                "engine": "SESSION_LIQUIDITY_TRANSFER",
                "asia_start_minute_utc": 0,
                "asia_end_minute_utc": 360,
                "london_start_minute_utc": 420,
                "london_end_minute_utc": 660,
                "new_york_start_minute_utc": 780,
                "new_york_end_minute_utc": 1020,
                "session_use_asia_levels": True,
                "session_use_previous_day_levels": False,
                "session_use_flow_proxy": True,
                "session_sweep_min_atr": 0.10,
                "session_response_bars": 3,
                "session_reclaim_tolerance_atr": 0.05,
                "session_response_body_atr": 0.30,
                "session_response_flow_ratio": 0.05,
                "session_acceptance_close_atr": 0.12,
                "session_acceptance_body_atr": 0.45,
                "session_acceptance_flow_ratio": 0.08,
                "minimum_structural_rr": 1.25,
                "stop_buffer_atr": 0.10,
                "cooldown_bars": 1,
            }
        )
        self.engine = SessionLiquidityTransferEngine(params)

    def test_completed_asia_high_sweep_requires_later_response(self) -> None:
        self.engine.observe(snap(0, ns(2024, 2, 26, 0, 1), 98.0, 101.0, 95.0, 100.0, 0.0), allow_new=True)
        self.engine.observe(snap(1, ns(2024, 2, 26, 5, 59), 100.0, 100.5, 96.0, 100.0, 0.0), allow_new=True)
        sweep = self.engine.observe(snap(2, ns(2024, 2, 26, 7, 1), 100.0, 101.5, 99.8, 101.2, 0.8), allow_new=True)
        self.assertIsNone(sweep.signal)
        self.assertEqual(sweep.transitions[0].next_state, "UPPER_SESSION_SWEEP_RESPONSE_OBSERVATION")

        response = self.engine.observe(snap(3, ns(2024, 2, 26, 7, 2), 101.0, 101.1, 99.0, 99.5, -0.8), allow_new=True)
        self.assertIsNotNone(response.signal)
        assert response.signal is not None
        self.assertEqual(response.signal.family, "SRR")
        self.assertEqual(response.signal.direction, "SHORT")
        self.assertEqual(response.signal.details["level_name"], "ASIA_HIGH")
        self.assertEqual(response.transitions[0].previous_state, "UPPER_SESSION_SWEEP_RESPONSE_OBSERVATION")
        self.assertEqual(response.transitions[-1].next_state, "ENTRY_ARMED")

    def test_no_session_signal_during_range_formation(self) -> None:
        for index, minute in enumerate((1, 60, 120, 240, 359)):
            result = self.engine.observe(
                snap(index, ns(2024, 2, 27, minute // 60, minute % 60), 100.0, 103.0, 97.0, 101.0, 0.8),
                allow_new=True,
            )
            self.assertIsNone(result.signal)
            self.assertFalse(result.transitions)


if __name__ == "__main__":
    unittest.main()
