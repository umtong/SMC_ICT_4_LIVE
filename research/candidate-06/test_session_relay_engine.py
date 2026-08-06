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
from session_relay_engine import SessionLiquidityRelayEngine


def ns(hour: int, minute: int) -> int:
    return int(datetime(2024, 2, 26, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def snap(index: int, timestamp: int, open_: float, high: float, low: float, close: float, flow: float, lower_fast: float = 100.5) -> PrimitiveSnapshot:
    width = high - low
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(timestamp, open_, high, low, close, 1000.0, 1000.0 * (flow + 1.0) / 2.0, 100),
        ready=True,
        atr=1.0,
        rel_volume=2.0,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=max(high - max(open_, close), 0.0) / width,
        lower_wick_fraction=max(min(open_, close) - low, 0.0) / width,
        close_location=(close - low) / width,
        upper_fast=106.0,
        lower_fast=lower_fast,
        upper_slow=108.0,
        lower_slow=96.0,
        slow_mid=102.0,
        range_position=(close - 96.0) / 12.0,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


class SessionRelayTests(unittest.TestCase):
    def test_new_york_can_trade_completed_london_high(self) -> None:
        params = json.loads((HERE / "config.json").read_text(encoding="utf-8"))["logic"]
        params.update(
            {
                "engine": "SESSION_LIQUIDITY_RELAY",
                "asia_start_minute_utc": 0,
                "asia_end_minute_utc": 360,
                "london_start_minute_utc": 420,
                "london_end_minute_utc": 660,
                "new_york_start_minute_utc": 780,
                "new_york_end_minute_utc": 1020,
                "london_range_start_minute_utc": 420,
                "london_range_end_minute_utc": 720,
                "session_use_asia_levels": False,
                "session_use_previous_day_levels": False,
                "session_use_london_levels": True,
                "session_use_flow_proxy": True,
                "session_sweep_min_atr": 0.10,
                "session_response_bars": 3,
                "session_displacement_body_atr": 0.45,
                "session_displacement_close_atr": 0.08,
                "session_displacement_flow_ratio": 0.05,
                "session_displacement_retest_bars": 6,
                "session_displacement_retrace_fraction": 0.50,
                "session_displacement_max_opposing_flow": 0.22,
                "session_retest_rejection_body_atr": 0.12,
                "minimum_structural_rr": 1.10,
                "stop_buffer_atr": 0.10,
                "enable_srr": True,
                "enable_sac": False,
            }
        )
        engine = SessionLiquidityRelayEngine(params)
        engine.observe(snap(0, ns(7, 0), 101.0, 105.0, 98.0, 103.0, 0.0), allow_new=True)
        engine.observe(snap(1, ns(11, 59), 103.0, 104.0, 99.0, 102.0, 0.0), allow_new=True)

        sweep = engine.observe(snap(2, ns(13, 1), 104.0, 106.0, 103.8, 105.5, 0.8), allow_new=True)
        self.assertIsNone(sweep.signal)
        self.assertEqual(sweep.transitions[0].details["level_name"], "LONDON_HIGH")

        displacement = engine.observe(snap(3, ns(13, 2), 105.0, 105.1, 102.5, 103.0, -0.8), allow_new=True)
        self.assertIsNone(displacement.signal)
        retrace = engine.observe(snap(4, ns(13, 3), 103.8, 104.5, 103.2, 103.6, 0.0), allow_new=True)
        self.assertIsNotNone(retrace.signal)
        assert retrace.signal is not None
        self.assertEqual(retrace.signal.details["level_name"], "LONDON_HIGH")
        self.assertEqual(retrace.signal.direction, "SHORT")


if __name__ == "__main__":
    unittest.main()
