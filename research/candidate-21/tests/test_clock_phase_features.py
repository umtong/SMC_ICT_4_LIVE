from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from clock_phase_features import enrich_clock_features


class ClockPhaseFeatureTests(unittest.TestCase):
    @staticmethod
    def fixture(count: int = 40) -> tuple[pd.DataFrame, pd.DataFrame]:
        minutes = pd.date_range("2025-01-01", periods=count, freq="15min", tz="UTC")
        observed = minutes + pd.Timedelta(seconds=59, milliseconds=999)
        features = pd.DataFrame(
            {
                "observed_time_ns": observed.astype("int64"),
                "feature_ready": True,
            },
        )
        notionals = np.full(count, 100.0)
        notionals[-1] = 200.0
        opening = pd.DataFrame(
            {
                "qh_open_price": 100.0,
                "qh_open_high": 100.2,
                "qh_open_low": 99.9,
                "qh_open_close": 100.1,
                "qh_open_notional": notionals,
                "qh_open_signed_notional": notionals * 0.2,
                "qh_open_trade_count": 50,
                "qh_open_return_bps": 10.0,
                "qh_open_range_bps": 30.0,
                "qh_open_impact_efficiency": 1.0 / 3.0,
                "qh_open_flow": 0.2,
            },
            index=minutes,
        )
        return features, opening

    def test_baseline_excludes_current_boundary(self) -> None:
        features, opening = self.fixture()
        result = enrich_clock_features(
            features,
            opening,
            period_minutes=15,
            baseline_periods=96,
            min_baseline_samples=32,
        )
        self.assertAlmostEqual(float(result.iloc[-1]["qh_open_notional_baseline"]), 100.0)
        self.assertAlmostEqual(float(result.iloc[-1]["qh_open_notional_burst"]), 2.0)
        self.assertEqual(float(result.iloc[-1]["qh_phase_sample_count"]), 39.0)
        self.assertTrue(bool(result.iloc[-1]["qh_feature_ready"]))

    def test_future_mutation_cannot_change_past_feature(self) -> None:
        features, opening = self.fixture()
        original = enrich_clock_features(
            features,
            opening,
            period_minutes=15,
            baseline_periods=12,
            min_baseline_samples=4,
        )
        opening_mutated = opening.copy()
        opening_mutated.iloc[-1, opening_mutated.columns.get_loc("qh_open_notional")] = 1e9
        mutated = enrich_clock_features(
            features,
            opening_mutated,
            period_minutes=15,
            baseline_periods=12,
            min_baseline_samples=4,
        )
        pd.testing.assert_series_equal(
            original.iloc[:-1]["qh_open_notional_burst"].reset_index(drop=True),
            mutated.iloc[:-1]["qh_open_notional_burst"].reset_index(drop=True),
        )

    def test_non_boundary_rows_are_not_clock_ready(self) -> None:
        minutes = pd.date_range("2025-01-01 00:01", periods=40, freq="min", tz="UTC")
        observed = minutes + pd.Timedelta(seconds=59)
        features = pd.DataFrame(
            {"observed_time_ns": observed.astype("int64"), "feature_ready": True},
        )
        opening = pd.DataFrame(
            {
                "qh_open_price": 100.0,
                "qh_open_high": 100.1,
                "qh_open_low": 99.9,
                "qh_open_close": 100.0,
                "qh_open_notional": 100.0,
                "qh_open_signed_notional": 10.0,
                "qh_open_trade_count": 10,
                "qh_open_return_bps": 0.0,
                "qh_open_range_bps": 20.0,
                "qh_open_impact_efficiency": 0.0,
                "qh_open_flow": 0.1,
            },
            index=minutes,
        )
        result = enrich_clock_features(
            features,
            opening,
            period_minutes=15,
            baseline_periods=4,
            min_baseline_samples=1,
        )
        for _, row in result.iterrows():
            minute = pd.to_datetime(int(row["observed_time_ns"]), unit="ns", utc=True).minute
            if minute % 15 != 0:
                self.assertFalse(bool(row["qh_feature_ready"]))


if __name__ == "__main__":
    unittest.main()
