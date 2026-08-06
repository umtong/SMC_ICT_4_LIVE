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
from auction_relay_engine import RollingAuctionLiquidityRelayEngine


def ns(hour: int, minute: int) -> int:
    return int(datetime(2024, 2, 26, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def snap(index: int, timestamp: int, open_: float, high: float, low: float, close: float, flow: float, lower_fast: float = 96.5) -> PrimitiveSnapshot:
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
        upper_fast=103.0,
        lower_fast=lower_fast,
        upper_slow=105.0,
        lower_slow=90.0,
        slow_mid=97.5,
        range_position=(close - 90.0) / 15.0,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


class RollingAuctionRelayTests(unittest.TestCase):
    def test_only_completed_previous_hour_can_be_swept(self) -> None:
        params = json.loads((HERE / "config.json").read_text(encoding="utf-8"))["logic"]
        params.update(
            {
                "auction_entry_window_minutes": 55,
                "auction_sweep_min_atr": 0.10,
                "session_use_flow_proxy": True,
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
        engine = RollingAuctionLiquidityRelayEngine(params)
        # Event 00:01 is the completed 00:00-00:01 source bar. Event 01:00
        # remains in the source hour 00 and must finish that auction.
        first = engine.observe(snap(0, ns(0, 1), 98.0, 101.0, 95.0, 100.0, 0.0), allow_new=True)
        last = engine.observe(snap(1, ns(1, 0), 100.0, 100.5, 96.0, 100.0, 0.0), allow_new=True)
        self.assertFalse(first.transitions)
        self.assertFalse(last.transitions)

        sweep = engine.observe(snap(2, ns(1, 1), 100.0, 102.0, 99.8, 101.5, 0.8), allow_new=True)
        self.assertIsNone(sweep.signal)
        self.assertEqual(sweep.transitions[0].details["level_name"], "PREVIOUS_HOUR_HIGH")

        displacement = engine.observe(snap(3, ns(1, 2), 101.0, 101.1, 98.5, 99.0, -0.8), allow_new=True)
        self.assertIsNone(displacement.signal)
        retrace = engine.observe(snap(4, ns(1, 3), 99.8, 100.5, 99.3, 99.6, 0.0), allow_new=True)
        self.assertIsNotNone(retrace.signal)
        assert retrace.signal is not None
        self.assertEqual(retrace.signal.direction, "SHORT")
        self.assertEqual(retrace.signal.details["level_name"], "PREVIOUS_HOUR_HIGH")


if __name__ == "__main__":
    unittest.main()
