from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from fixed_interval_auction_engine import FixedIntervalAuctionLiquidityRelayEngine
from lrb_types import BarObservation, PrimitiveSnapshot


def ns(hour: int, minute: int) -> int:
    return int(datetime(2024, 2, 26, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def snap(index: int, timestamp: int, open_: float, high: float, low: float, close: float, flow: float) -> PrimitiveSnapshot:
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
        lower_fast=96.5,
        upper_slow=105.0,
        lower_slow=90.0,
        slow_mid=97.5,
        range_position=(close - 90.0) / 15.0,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


class FixedIntervalAuctionTests(unittest.TestCase):
    def params(self, period: int = 30):
        params = json.loads((HERE / "config.json").read_text(encoding="utf-8"))["logic"]
        params.update(
            {
                "auction_period_minutes": period,
                "auction_entry_window_minutes": max(1, period - 5),
                "auction_sweep_min_atr": 0.10,
                "session_use_flow_proxy": True,
                "session_response_bars": 3,
                "session_acceptance_body_atr": 0.45,
                "session_acceptance_close_atr": 0.08,
                "session_acceptance_flow_ratio": 0.05,
                "session_retest_bars": 7,
                "session_retest_band_atr": 0.18,
                "session_acceptance_reclaim_atr": 0.08,
                "session_retest_max_opposing_flow": 0.20,
                "enable_srr": False,
                "enable_sac": True,
            },
        )
        return params

    def test_previous_30m_bucket_is_not_visible_until_complete(self) -> None:
        engine = FixedIntervalAuctionLiquidityRelayEngine(self.params(30))
        first = engine.observe(snap(0, ns(0, 1), 98.0, 101.0, 95.0, 100.0, 0.0), allow_new=True)
        last = engine.observe(snap(1, ns(0, 30), 100.0, 102.0, 96.0, 101.0, 0.0), allow_new=True)
        self.assertFalse(first.transitions)
        self.assertFalse(last.transitions)
        # Event 00:30 is the completed 00:29 source bar. Only event 00:31,
        # whose source interval begins at 00:30, may sweep the completed bucket.
        sweep = engine.observe(snap(2, ns(0, 31), 101.0, 103.0, 100.5, 102.5, 0.8), allow_new=True)
        self.assertEqual(sweep.transitions[0].details["level_name"], "PREVIOUS_30M_AUCTION_HIGH")

    def test_quarter_hour_boundary_obeys_completed_bar_clock(self) -> None:
        engine = FixedIntervalAuctionLiquidityRelayEngine(self.params(15))
        first = engine.observe(snap(0, ns(0, 1), 98.0, 101.0, 95.0, 100.0, 0.0), allow_new=True)
        last = engine.observe(snap(1, ns(0, 15), 100.0, 102.0, 96.0, 101.0, 0.0), allow_new=True)
        self.assertFalse(first.transitions)
        self.assertFalse(last.transitions)
        sweep = engine.observe(snap(2, ns(0, 16), 101.0, 103.0, 100.5, 102.5, 0.8), allow_new=True)
        self.assertEqual(sweep.transitions[0].details["level_name"], "PREVIOUS_15M_AUCTION_HIGH")

    def test_period_must_divide_utc_day(self) -> None:
        params = self.params(30)
        params["auction_period_minutes"] = 35
        with self.assertRaises(ValueError):
            FixedIntervalAuctionLiquidityRelayEngine(params)


if __name__ == "__main__":
    unittest.main()
