"""Pure contracts for duplicate-timestamp-safe quote resiliency features."""

from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from quote_resiliency_features_v3 import (
    IMPLEMENTATION_REVISION,
    QuoteResiliencyConfig,
    aggregate_quote_events,
    build_quote_resiliency_features,
    quote_event_rows,
)


class DuplicateTimestampRawQuoteContracts(unittest.TestCase):
    @staticmethod
    def _quotes() -> pd.DataFrame:
        index = pd.DatetimeIndex(
            [
                "2023-10-15T00:00:01.000Z",
                "2023-10-15T00:00:01.000Z",
                "2023-10-15T00:00:01.001Z",
                "2023-10-15T00:00:01.001Z",
                "2023-10-15T00:00:09.999Z",
            ]
        )
        return pd.DataFrame(
            {
                "best_bid_price": [100.0, 100.0, 100.0, 99.9, 100.0],
                "best_bid_qty": [11.0, 13.0, 12.0, 8.0, 5.0],
                "best_ask_price": [100.1, 100.1, 100.1, 100.1, 100.1],
                "best_ask_qty": [8.0, 7.0, 10.0, 10.0, 6.0],
            },
            index=index,
        )

    @staticmethod
    def _previous() -> dict[str, float]:
        return {
            "best_bid_price": 100.0,
            "best_bid_qty": 10.0,
            "best_ask_price": 100.1,
            "best_ask_qty": 8.0,
        }

    def test_duplicate_exchange_milliseconds_are_distinct_ordered_events(self) -> None:
        events, final = quote_event_rows(self._quotes(), previous_quote=self._previous())
        self.assertEqual(len(events.index), 5)
        self.assertTrue(events.index.has_duplicates)
        self.assertEqual(events["bid_add_qty"].tolist(), [1.0, 2.0, 0.0, 0.0, 5.0])
        self.assertEqual(events["bid_remove_qty"].tolist(), [0.0, 0.0, 1.0, 12.0, 0.0])
        self.assertEqual(events["ask_add_qty"].tolist(), [0.0, 0.0, 3.0, 0.0, 0.0])
        self.assertEqual(events["ask_remove_qty"].tolist(), [0.0, 1.0, 0.0, 0.0, 4.0])
        self.assertEqual(events["quote_ofi_qty"].tolist(), [1.0, 3.0, -4.0, -12.0, 9.0])
        self.assertEqual(final["best_bid_price"], 100.0)
        self.assertEqual(final["best_ask_qty"], 6.0)

    def test_chunk_split_inside_equal_timestamp_matches_single_pass(self) -> None:
        quotes = self._quotes()
        whole, _ = quote_event_rows(quotes, previous_quote=self._previous())
        first, state = quote_event_rows(quotes.iloc[:1], previous_quote=self._previous())
        second, _ = quote_event_rows(quotes.iloc[1:], previous_quote=state)
        combined = pd.concat([first, second])
        pd.testing.assert_frame_equal(whole, combined)

    def test_completed_bucket_preserves_duplicate_event_count_and_state_order(self) -> None:
        events, _ = quote_event_rows(self._quotes(), previous_quote=self._previous())
        bucket = aggregate_quote_events(events, cadence_seconds=10)
        self.assertEqual(bucket.index.tolist(), [pd.Timestamp("2023-10-15T00:00:10Z")])
        self.assertEqual(float(bucket.iloc[0]["quote_update_count"]), 5.0)
        self.assertEqual(float(bucket.iloc[0]["bid_open"]), 100.0)
        self.assertEqual(float(bucket.iloc[0]["bid_close"]), 100.0)
        self.assertEqual(float(bucket.iloc[0]["bid_qty_open"]), 11.0)
        self.assertEqual(float(bucket.iloc[0]["bid_qty_close"]), 5.0)
        self.assertEqual(float(bucket.iloc[0]["quote_ofi_qty"]), -3.0)

    def test_raw_time_regression_is_rejected_without_deduplication(self) -> None:
        quotes = self._quotes().iloc[[0, 2, 1, 3, 4]].copy()
        with self.assertRaisesRegex(ValueError, "nondecreasing"):
            quote_event_rows(quotes, previous_quote=self._previous())


class CompletedFeatureContracts(unittest.TestCase):
    @staticmethod
    def _inputs(rows: int = 30) -> tuple[pd.DataFrame, pd.DataFrame, QuoteResiliencyConfig]:
        index = pd.date_range("2023-10-15T00:00:10Z", periods=rows, freq="10s")
        close = 100.0 + np.arange(rows, dtype=float) * 0.01
        trade = pd.DataFrame(
            {
                "open": close - 0.01,
                "high": close + 0.02,
                "low": close - 0.02,
                "close": close,
                "volume": np.full(rows, 10.0),
                "signed_volume": np.where(np.arange(rows) % 2 == 0, 2.0, -2.0),
                "trade_count": np.full(rows, 20.0),
            },
            index=index,
        )
        quote = pd.DataFrame(
            {
                "bid_open": close - 0.06,
                "bid_close": close - 0.05,
                "bid_qty_open": np.full(rows, 8.0),
                "bid_qty_close": np.full(rows, 9.0),
                "ask_open": close + 0.04,
                "ask_close": close + 0.05,
                "ask_qty_open": np.full(rows, 7.0),
                "ask_qty_close": np.full(rows, 6.0),
                "mid_open": close - 0.01,
                "mid_high": close + 0.01,
                "mid_low": close - 0.02,
                "mid_close": close,
                "microprice_close": close + 0.005,
                "quote_imbalance_close": np.full(rows, 0.2),
                "spread_open": np.full(rows, 0.1),
                "spread_max": np.full(rows, 0.2),
                "spread_median": np.full(rows, 0.1),
                "spread_close": np.full(rows, 0.1),
                "bid_add_qty": np.full(rows, 3.0),
                "bid_remove_qty": np.full(rows, 1.0),
                "ask_add_qty": np.full(rows, 1.0),
                "ask_remove_qty": np.full(rows, 2.0),
                "quote_ofi_qty": np.where(np.arange(rows) % 2 == 0, 3.0, -3.0),
                "quote_update_count": np.full(rows, 100.0),
                "quote_price_change_count": np.full(rows, 2.0),
                "quote_size_only_change_count": np.full(rows, 98.0),
            },
            index=index,
        )
        return trade, quote, QuoteResiliencyConfig(
            baseline_bars=10,
            minimum_history_bars=5,
        )

    def test_completed_feature_index_stays_exact_and_unique(self) -> None:
        trade, quote, config = self._inputs()
        result = build_quote_resiliency_features(
            trade_bars=trade,
            quote_buckets=quote,
            tick=0.1,
            config=config,
        )
        self.assertFalse(result.index.has_duplicates)
        self.assertEqual(result.attrs["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(
            result.attrs["raw_quote_timestamp_contract"],
            "DUPLICATES_ALLOWED_ORDERED_BY_TRANSACTION_TIME_THEN_UPDATE_ID",
        )

    def test_duplicate_completed_trade_timestamp_remains_invalid(self) -> None:
        trade, quote, config = self._inputs()
        broken = pd.concat([trade.iloc[:5], trade.iloc[[4]], trade.iloc[5:]]).sort_index()
        with self.assertRaisesRegex(ValueError, "timestamps must be unique"):
            build_quote_resiliency_features(
                trade_bars=broken,
                quote_buckets=quote,
                tick=0.1,
                config=config,
            )

    def test_source_contains_no_outcome_or_execution_logic(self) -> None:
        source = Path(__file__).with_name("quote_resiliency_features_v3.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "realized_pnl",
            "future_high",
            "future_low",
            "win_rate",
            "profit_factor",
            "model_score",
            "risk_multiplier",
            "BacktestEngine(",
            "submit_order",
            "order_factory",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
