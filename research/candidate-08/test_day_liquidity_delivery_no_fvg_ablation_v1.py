"""Contract tests for the single no-standard-FVG diagnostic ablation."""

from __future__ import annotations

import unittest

import numpy as np

import day_liquidity_delivery_no_fvg_ablation_v1 as ablation
import day_liquidity_delivery_signals_v1 as base
from day_liquidity_delivery_context_v1 import Swing
from range_fvg_logic import FiveMinuteBar


def _bar(index: int, *, open_: float, high: float, low: float, close: float) -> FiveMinuteBar:
    return FiveMinuteBar(
        index=index,
        ts_event_ns=(index + 1) * 300 * 1_000_000_000,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        trade_count=100.0,
        taker_buy_volume=50.0,
        imbalance=0.0,
        atr=1.0,
        volume_ratio=1.0,
        trade_ratio=1.0,
        efficiency_60m=0.0,
        direction_60m=0,
        session_key="SYNTHETIC",
        day_key="1970-01-01",
        week_key="1970-W01",
    )


class NoFVGAblationTests(unittest.TestCase):
    def test_only_gap_requirement_is_removed(self) -> None:
        bars = tuple(
            _bar(i, open_=100.0, high=100.4, low=99.6, close=100.1)
            for i in range(14)
        )
        mutable = list(bars)
        # Break the frozen high with displacement, but overlap the bar two positions back so there
        # is no standard bullish three-bar FVG.
        mutable[13] = _bar(13, open_=100.2, high=102.1, low=100.2, close=102.0)
        bars = tuple(mutable)
        swing = Swing(
            kind="FIVE_HIGH",
            price=101.0,
            formed_index=5,
            formed_time_ns=6 * 300 * 1_000_000_000,
            confirmed_index=7,
            confirmed_time_ns=8 * 300 * 1_000_000_000,
        )
        prior_body = np.full(len(bars), 0.2)
        prior_range = np.full(len(bars), 0.8)

        standard = base._five_displacement_fvg(
            bars=bars,
            position=13,
            direction=1,
            frozen_swing=swing,
            prior_body_median=prior_body,
            prior_range_median=prior_range,
            close_location=2.0 / 3.0,
            tick=0.1,
        )
        diagnostic = ablation._five_displacement_broken_swing_retest(
            bars=bars,
            position=13,
            direction=1,
            frozen_swing=swing,
            prior_body_median=prior_body,
            prior_range_median=prior_range,
            close_location=2.0 / 3.0,
            tick=0.1,
        )
        self.assertIsNone(standard)
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic.fvg_low, swing.price)
        self.assertEqual(diagnostic.fvg_high, swing.price)

    def test_non_displacement_still_fails(self) -> None:
        bars = tuple(
            _bar(i, open_=100.0, high=100.4, low=99.6, close=100.1)
            for i in range(14)
        )
        swing = Swing(
            kind="FIVE_HIGH",
            price=101.0,
            formed_index=5,
            formed_time_ns=6 * 300 * 1_000_000_000,
            confirmed_index=7,
            confirmed_time_ns=8 * 300 * 1_000_000_000,
        )
        prior_body = np.full(len(bars), 0.2)
        prior_range = np.full(len(bars), 0.8)
        self.assertIsNone(
            ablation._five_displacement_broken_swing_retest(
                bars=bars,
                position=13,
                direction=1,
                frozen_swing=swing,
                prior_body_median=prior_body,
                prior_range_median=prior_range,
                close_location=2.0 / 3.0,
                tick=0.1,
            )
        )

    def test_rejection_and_diagnostic_labels_are_truthful(self) -> None:
        raw = {
            "scenario_id": "x",
            "reason": "NO_FIVE_MINUTE_MSS_FVG_BEFORE_ROUTE_EXPIRY",
            "details": {},
        }
        transformed = ablation._renamed_rejection(raw)
        self.assertEqual(
            transformed["reason"],
            "NO_FIVE_MINUTE_MSS_DISPLACEMENT_BEFORE_ROUTE_EXPIRY",
        )
        self.assertTrue(transformed["details"]["diagnostic_only"])


if __name__ == "__main__":
    unittest.main()
