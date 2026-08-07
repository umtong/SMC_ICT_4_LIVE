from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from derive_nt_lvcfr_v27_signals import Event
from derive_nt_lvcfr_v28_signals import (
    find_retest,
    structural_label,
    train_rank_model,
)


class StructuralLabelTests(unittest.TestCase):
    def bars(self, future_highs: list[float], future_lows: list[float]) -> pd.DataFrame:
        rows = [
            {
                "end_time_ms": 300_000,
                "futures_close": 100.0,
                "futures_high": 100.2,
                "futures_low": 99.8,
            }
        ]
        for index, (high, low) in enumerate(zip(future_highs, future_lows), 1):
            rows.append(
                {
                    "end_time_ms": 300_000 + index * 300_000,
                    "futures_close": (high + low) / 2.0,
                    "futures_high": high,
                    "futures_low": low,
                }
            )
        return pd.DataFrame(rows)

    def event(self, side: int) -> Event:
        return Event(
            event_end_ms=0,
            confirmation_end_ms=300_000,
            side=side,
            level=100.0,
            event_high=101.0,
            event_low=99.0,
            confirmation_high=100.2,
            confirmation_low=99.8,
            atr=1.0,
            features=(0.0,) * 16,
        )

    def test_up_barrier_is_continuation_for_high_breach(self) -> None:
        bars = self.bars([100.9], [99.9])
        self.assertEqual(structural_label(bars, {300_000: 0}, self.event(1)), 1)

    def test_up_barrier_is_reversal_for_low_breach(self) -> None:
        bars = self.bars([100.9], [99.9])
        self.assertEqual(structural_label(bars, {300_000: 0}, self.event(-1)), -1)

    def test_same_bar_two_sided_barrier_is_ambiguous(self) -> None:
        bars = self.bars([100.9], [99.1])
        self.assertEqual(structural_label(bars, {300_000: 0}, self.event(1)), 0)


class RetestTests(unittest.TestCase):
    def minutes(self, rows: list[tuple[int, float, float, float, float, float, float]]) -> pd.DataFrame:
        data = []
        for minute, open_, high, low, close, futures_flow, spot_flow in rows:
            quote = 1_000_000.0
            data.append(
                {
                    "open_time_ms": minute * 60_000,
                    "futures_open": open_,
                    "futures_high": high,
                    "futures_low": low,
                    "futures_close": close,
                    "futures_quote": quote,
                    "futures_taker_buy_quote": quote * (futures_flow + 1.0) / 2.0,
                    "spot_open": open_,
                    "spot_high": high,
                    "spot_low": low,
                    "spot_close": close,
                    "spot_quote": quote,
                    "spot_taker_buy_quote": quote * (spot_flow + 1.0) / 2.0,
                }
            )
        return pd.DataFrame(data)

    def event(self, side: int) -> Event:
        return Event(
            event_end_ms=240_000,
            confirmation_end_ms=300_000,
            side=side,
            level=100.0,
            event_high=101.0,
            event_low=99.0,
            confirmation_high=101.2,
            confirmation_low=100.1,
            atr=1.0,
            features=(0.0,) * 16,
        )

    def test_continuation_waits_for_completed_retest_defense(self) -> None:
        minutes = self.minutes(
            [
                (4, 100.4, 101.2, 100.1, 101.0, 0.3, 0.2),
                (5, 100.4, 100.7, 100.2, 100.6, 0.4, 0.3),
            ]
        )
        retest, reason = find_retest(minutes, self.event(1), 1)
        self.assertIsNotNone(retest)
        self.assertEqual(int(retest.open_time_ms), 300_000)
        self.assertEqual(reason, "CONTINUATION_RETEST_DEFENDED")

    def test_reversal_requires_reclaim_and_failed_retest(self) -> None:
        minutes = self.minutes(
            [
                (4, 100.4, 101.2, 100.1, 101.0, 0.3, 0.2),
                (5, 100.1, 100.3, 99.7, 99.8, -0.4, -0.2),
            ]
        )
        retest, reason = find_retest(minutes, self.event(1), -1)
        self.assertIsNotNone(retest)
        self.assertEqual(reason, "REVERSAL_RECLAIM_RETEST_REJECTED")


class RankModelTests(unittest.TestCase):
    def test_separable_structural_state_calibrates(self) -> None:
        x = np.linspace(-3.0, 3.0, 400).reshape(-1, 1)
        y = (x[:, 0] > 0.0).astype(float)
        model = train_rank_model(x, y, "TEST")
        self.assertLess(model.model.threshold, 1.0)
        self.assertGreater(model.model.predict((2.0,)), model.model.predict((-2.0,)))
        self.assertGreaterEqual(model.model.calibration_count, 12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
