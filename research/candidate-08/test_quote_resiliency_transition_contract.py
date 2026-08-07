"""Regression contract: one completed bucket cannot terminate and re-arm scenarios."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quote_resiliency_features_v3 import QuoteResiliencyConfig
from quote_resiliency_signals import build_quote_resiliency_signals
from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, LevelSource


class OneScenarioTransitionPerBucketContract(unittest.TestCase):
    @staticmethod
    def _bar(index: int, timestamp: str) -> FiveMinuteBar:
        ts = pd.Timestamp(timestamp)
        return FiveMinuteBar(
            index=index,
            ts_event_ns=int(ts.as_unit("ns").value),
            open=100.0,
            high=103.0,
            low=97.0,
            close=100.0,
            volume=100.0,
            trade_count=100.0,
            taker_buy_volume=50.0,
            imbalance=0.0,
            atr=1.0,
            volume_ratio=1.0,
            trade_ratio=1.0,
            efficiency_60m=0.0,
            direction_60m=0.0,
            session_key="s",
            day_key="d",
            week_key="w",
        )

    def test_rejected_response_bucket_cannot_arm_another_crossed_level(self) -> None:
        bars = (
            self._bar(0, "2023-10-15T00:00:00Z"),
            self._bar(1, "2023-10-15T00:05:00Z"),
            self._bar(2, "2023-10-15T00:10:00Z"),
        )
        context_times = np.asarray([bar.ts_event_ns for bar in bars], dtype=np.int64)
        levels = (
            ExternalLevel(
                level_id="day-high-100",
                kind=LevelKind.HIGH,
                source=LevelSource.DAY,
                level=100.0,
                formed_index=-1,
                formed_time_ns=0,
                period_key="prior-day",
            ),
            ExternalLevel(
                level_id="day-high-102",
                kind=LevelKind.HIGH,
                source=LevelSource.DAY,
                level=102.0,
                formed_index=-1,
                formed_time_ns=0,
                period_key="older-day",
            ),
            ExternalLevel(
                level_id="day-low-98",
                kind=LevelKind.LOW,
                source=LevelSource.DAY,
                level=98.0,
                formed_index=-1,
                formed_time_ns=0,
                period_key="older-low",
            ),
        )
        snapshots = (levels, levels, levels)
        index = pd.date_range("2023-10-15T00:05:10Z", periods=3, freq="10s")
        data = pd.DataFrame(
            [
                {
                    "open": 99.8,
                    "high": 99.9,
                    "low": 99.7,
                    "close": 99.8,
                    "aggressive_pressure_ratio": 0.0,
                },
                {
                    "open": 99.8,
                    "high": 100.3,
                    "low": 99.8,
                    "close": 100.1,
                    "aggressive_pressure_ratio": 1.2,
                },
                {
                    "open": 100.1,
                    "high": 102.3,
                    "low": 100.0,
                    "close": 102.1,
                    "aggressive_pressure_ratio": 1.5,
                },
            ],
            index=index,
        )
        for column in (
            "quote_ofi_ratio",
            "quote_ofi_qty",
            "bid_add_qty",
            "bid_remove_qty",
            "ask_add_qty",
            "ask_remove_qty",
        ):
            data[column] = 0.0
        data["bid_close"] = data["close"] - 0.1
        data["ask_close"] = data["close"] + 0.1
        data["spread_median_ratio"] = 1.0
        data["quote_resiliency_observable"] = True
        data["native_quote_snapshot_observable"] = True

        bundle = build_quote_resiliency_signals(
            data=data,
            context_times=context_times,
            context_bars=bars,
            snapshots=snapshots,
            symbol="BTCUSDT",
            instrument_id="BTCUSDT-PERP.BINANCE",
            tick=0.1,
            fee_rate=0.0,
            minimum_net_reward_risk=1.0,
            config=QuoteResiliencyConfig(response_window_bars=1),
        )

        self.assertEqual(bundle.diagnostics.get("EXTERNAL_INTERACTION_ARMED"), 1)
        self.assertEqual(bundle.diagnostics.get("LIQUIDITY_RESPONSE_NOT_CLASSIFIED"), 1)
        self.assertNotIn(
            "EVALUATION_ENDED_WITH_INCOMPLETE_SCENARIO",
            bundle.diagnostics,
        )
        self.assertEqual(len(bundle.rejected_scenarios), 1)
        self.assertEqual(
            bundle.rejected_scenarios[0]["reason"],
            "LIQUIDITY_RESPONSE_NOT_CLASSIFIED",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
