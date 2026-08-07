from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from derive_nt_lvcfr_v31_signals import (
    calibrate_threshold,
    feature_frame,
    target_trigger,
    train_direction,
)


class CostAwareLabelTests(unittest.TestCase):
    def test_target_is_symmetric_and_clears_one_point_twenty_five_r(self) -> None:
        long_target = target_trigger(100.0, 99.0, 1)
        short_target = target_trigger(100.0, 101.0, -1)
        self.assertGreater(long_target, 101.25)
        self.assertLess(short_target, 98.75)
        self.assertAlmostEqual(
            long_target - 100.0,
            100.0 - short_target,
            delta=0.03,
        )


class CalibrationTests(unittest.TestCase):
    def test_rank_threshold_requires_reliable_tail(self) -> None:
        scores = np.linspace(0.0, 1.0, 400)
        labels = (scores > 0.75).astype(int)
        threshold, count, wins, lower = calibrate_threshold(scores, labels)
        self.assertLessEqual(threshold, 1.0)
        self.assertGreaterEqual(count, 16)
        self.assertEqual(wins, count)
        self.assertGreaterEqual(lower, 0.50)

    def test_nonlinear_xor_is_learned_deterministically(self) -> None:
        rng = np.random.default_rng(31)
        x = rng.normal(size=(2000, 2))
        y = ((x[:, 0] > 0.0) ^ (x[:, 1] > 0.0)).astype(int)
        first = train_direction(x[:1600], y[:1600], x[1600:], y[1600:], 1)
        second = train_direction(x[:1600], y[:1600], x[1600:], y[1600:], 1)
        probe = np.asarray([[1.0, -1.0], [1.0, 1.0]])
        np.testing.assert_allclose(
            first.estimator.predict_proba(probe),
            second.estimator.predict_proba(probe),
        )
        self.assertGreater(first.score(probe[0]), first.score(probe[1]))
        self.assertGreaterEqual(first.calibration_count, 16)


class CausalFeatureTests(unittest.TestCase):
    def synthetic_bars(self) -> pd.DataFrame:
        rows = []
        for index in range(120):
            row = {"end_time_ms": (index + 1) * 180_000}
            for symbol in ("btcusdt", "ethusdt", "solusdt", "xrpusdt"):
                for market in ("futures", "spot"):
                    prefix = f"{symbol}_{market}"
                    price = 100.0 + index * 0.1 + (0.01 if market == "futures" else 0.0)
                    row[f"{prefix}_open"] = price - 0.05
                    row[f"{prefix}_high"] = price + 0.10
                    row[f"{prefix}_low"] = price - 0.10
                    row[f"{prefix}_close"] = price
                    row[f"{prefix}_quote"] = 1_000_000.0 + index * 1_000.0
                    row[f"{prefix}_flow"] = 0.10
                    row[f"{prefix}_return"] = 0.001
            row["common_return"] = 0.001
            row["short_vol"] = 0.001
            row["long_vol"] = 0.0015
            row["beta"] = 0.8
            row["btc_atr"] = 1.0
            rows.append(row)
        return pd.DataFrame(rows)

    def test_future_mutation_does_not_change_past_features(self) -> None:
        bars = self.synthetic_bars()
        baseline = feature_frame(bars)
        mutated = bars.copy()
        mutated.loc[100:, "btcusdt_futures_close"] *= 2.0
        changed = feature_frame(mutated)
        pd.testing.assert_series_equal(
            baseline.iloc[99],
            changed.iloc[99],
            check_names=False,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
