from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from spot_price_discovery_contract import build_spot_features


class SpotPriceDiscoveryContractTests(unittest.TestCase):
    def _inputs(self, periods: int = 180) -> tuple[pd.DataFrame, pd.DataFrame]:
        minute = pd.date_range("2024-01-01", periods=periods, freq="min", tz="UTC")
        price = 100.0 + np.arange(periods, dtype=float) * 0.01
        klines = pd.DataFrame(
            {
                "open_time_dt": minute,
                "close_time_dt": minute + pd.Timedelta(minutes=1) - pd.Timedelta(milliseconds=1),
                "open": price,
                "high": price + 0.02,
                "low": price - 0.02,
                "close": price + 0.01,
                "volume": 10.0,
                "quote_volume": 1_000.0,
            },
        )
        agg = pd.DataFrame(
            {
                "trade_open": price,
                "trade_high": price + 0.02,
                "trade_low": price - 0.01,
                "trade_close": price + 0.01,
                "quantity_60s": 10.0,
                "notional_60s": 1_000.0 + np.arange(periods, dtype=float),
                "signed_notional_60s": 300.0,
                "buy_notional_60s": 650.0,
                "sell_notional_60s": 350.0,
                "trade_count_60s": 100,
                "path_60s_bps": 5.0,
                "notional_15s": 250.0,
                "signed_notional_15s": 125.0,
                "trade_count_15s": 25,
                "path_15s_bps": 1.0,
                "notional_open_10s": 150.0,
                "signed_notional_open_10s": 50.0,
                "trade_count_open_10s": 15,
            },
            index=minute,
        )
        return klines, agg

    def test_completed_minute_spot_features_are_causal_and_monotonic(self) -> None:
        klines, agg = self._inputs()
        result = build_spot_features(klines, agg)
        self.assertTrue(result["spot_observed_time"].is_monotonic_increasing)
        self.assertFalse(result["spot_observed_time"].duplicated().any())
        self.assertEqual(
            result.iloc[0]["spot_observed_time"],
            klines.iloc[0]["close_time_dt"],
        )
        expected_full_flow = (
            float(agg.iloc[-1]["signed_notional_60s"])
            / float(agg.iloc[-1]["notional_60s"])
        )
        self.assertAlmostEqual(
            float(result.iloc[-1]["spot_flow_60s"]),
            expected_full_flow,
        )
        self.assertAlmostEqual(float(result.iloc[-1]["spot_flow_15s"]), 0.5)
        self.assertGreater(float(result.iloc[-1]["spot_flow_3m"]), 0.0)
        self.assertTrue(np.isfinite(float(result.iloc[-1]["spot_notional_burst"])))

    def test_missing_tail_uses_same_minute_full_flow_without_future_data(self) -> None:
        klines, agg = self._inputs()
        agg.loc[agg.index[10], "notional_15s"] = 0.0
        agg.loc[agg.index[10], "signed_notional_15s"] = 0.0
        result = build_spot_features(klines, agg)
        self.assertAlmostEqual(
            float(result.iloc[10]["spot_flow_15s"]),
            float(result.iloc[10]["spot_flow_60s"]),
        )

    def test_duplicate_completed_observation_is_rejected(self) -> None:
        klines, agg = self._inputs()
        duplicate = klines.iloc[[0]].copy()
        klines = pd.concat([klines, duplicate], ignore_index=True)
        with self.assertRaisesRegex(RuntimeError, "duplicate spot observation"):
            build_spot_features(klines, agg)


if __name__ == "__main__":
    unittest.main()
