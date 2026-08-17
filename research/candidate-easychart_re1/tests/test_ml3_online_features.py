from __future__ import annotations

import math
from types import SimpleNamespace
import unittest

import pandas as pd

from counterfactual_sequential_features import build_sequential_state
from ml3_online_features import (
    CATEGORICAL_FEATURES,
    CausalOHLCVState,
    MinuteBar,
    NUMERIC_FEATURES,
    REQUIRED_SYMBOLS,
    offline_feature_row,
)


class ML3OnlineFeatureTest(unittest.TestCase):
    @staticmethod
    def _frames() -> dict[str, pd.DataFrame]:
        opens = pd.date_range("2025-01-01", periods=330, freq="1min", tz="UTC")
        output: dict[str, pd.DataFrame] = {}
        for symbol_index, symbol in enumerate(REQUIRED_SYMBOLS):
            price = 100.0 + 30.0 * symbol_index
            records = []
            for index, timestamp in enumerate(opens):
                bar_open = price
                increment = 0.00008 + 0.00022 * math.sin(
                    0.17 * index + 0.41 * symbol_index
                )
                price = bar_open * math.exp(increment)
                high = max(bar_open, price) * 1.00025
                low = min(bar_open, price) * 0.99975
                quote_volume = 1_000_000.0 * (
                    1.0 + 0.2 * math.sin(0.09 * index + symbol_index)
                )
                delta_share = 0.3 * math.sin(0.13 * index + symbol_index)
                taker_buy_quote = 0.5 * quote_volume * (1.0 + delta_share)
                records.append(
                    {
                        "open_time_dt": timestamp,
                        "open": bar_open,
                        "high": high,
                        "low": low,
                        "close": price,
                        "volume": quote_volume / price,
                        "quote_volume": quote_volume,
                        "count": 1000 + (index % 100),
                        "taker_buy_base_volume": taker_buy_quote / price,
                        "taker_buy_quote_volume": taker_buy_quote,
                    }
                )
            output[symbol] = pd.DataFrame(records)
        return output

    def test_online_and_offline_transform_match(self) -> None:
        frames = self._frames()
        state = CausalOHLCVState(REQUIRED_SYMBOLS)
        for row_index in range(len(next(iter(frames.values())))):
            bars = {}
            for symbol in REQUIRED_SYMBOLS:
                row = frames[symbol].iloc[row_index]
                close_time = pd.Timestamp(row["open_time_dt"]) + pd.Timedelta(minutes=1)
                bars[symbol] = MinuteBar(
                    ts_ns=int(close_time.value),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            state.observe_synchronized(bars)

        symbol = "BTCUSDT"
        final_close = float(frames[symbol].iloc[-1]["close"])
        observed_time_ns = int(
            (pd.Timestamp(frames[symbol].iloc[-1]["open_time_dt"]) + pd.Timedelta(minutes=1)).value
        )
        risk = final_close * 0.005
        plan = SimpleNamespace(
            symbol=symbol,
            side="LONG",
            entry=final_close,
            stop=final_close - risk,
            target=final_close + 1.4 * risk,
            gross_rr=1.4,
            observed_time_ns=observed_time_ns,
            interaction_time_ns=observed_time_ns - 2 * 60_000_000_000,
            trigger_time_ns=observed_time_ns - 60_000_000_000,
            setup_observed_time_ns=observed_time_ns - 5 * 60_000_000_000,
            overlap_lower=final_close - 0.2 * risk,
            overlap_upper=final_close + 0.1 * risk,
            higher_strength_ratio=1.7,
            lower_strength_ratio=1.4,
            trigger_strength_ratio=2.1,
            source_rule_count=4,
            family="HORIZONTAL_FLIP",
            scenario_path="ACCEPTANCE",
            scale_name="15_5_1",
            higher_zone_kind="HORIZONTAL_RESISTANCE",
            lower_zone_kind="ORDER_BLOCK",
            trigger_zone_kind="FVG",
            target_zone_kind="SWING_HIGH",
        )
        online = state.plan_features(plan)

        sequential = build_sequential_state(frames)
        timestamp = pd.Timestamp(observed_time_ns, unit="ns", tz="UTC")
        offline_record = dict(vars(plan))
        offline_record.update(sequential.loc[(symbol, timestamp)].to_dict())
        offline = offline_feature_row(offline_record)

        self.assertEqual(set(online), set(NUMERIC_FEATURES) | set(CATEGORICAL_FEATURES))
        for name in NUMERIC_FEATURES:
            self.assertAlmostEqual(float(online[name]), float(offline[name]), places=9, msg=name)
        for name in CATEGORICAL_FEATURES:
            self.assertEqual(online[name], offline[name], name)


if __name__ == "__main__":
    unittest.main()
