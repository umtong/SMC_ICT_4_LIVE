"""Pure streaming and integrity contracts for quote resiliency data."""

from __future__ import annotations

from pathlib import Path
import unittest

import pandas as pd

from data import BinanceDataError
from quote_resiliency_data import (
    _coerce_ordered_raw_chunk,
    aggregate_ordered_raw_quote_chunks,
)
from quote_resiliency_data_v2 import DATA_REVISION


BOOK_COLUMNS = (
    "update_id",
    "best_bid_price",
    "best_bid_qty",
    "best_ask_price",
    "best_ask_qty",
    "transaction_time",
    "event_time",
)


class RawChunkIntegrityContracts(unittest.TestCase):
    @staticmethod
    def _raw() -> pd.DataFrame:
        return pd.DataFrame(
            [
                [1, 100.0, 10.0, 100.1, 8.0, 1_697_328_001_000, 1_697_328_001_001],
                [2, 100.0, 12.0, 100.1, 8.0, 1_697_328_001_000, 1_697_328_001_002],
                [3, 100.0, 11.0, 100.1, 9.0, 1_697_328_001_001, 1_697_328_001_003],
            ],
            columns=BOOK_COLUMNS,
        )

    def test_equal_transaction_millisecond_preserves_update_order(self) -> None:
        chunk, quality = _coerce_ordered_raw_chunk(
            self._raw(),
            previous_transaction_ms=None,
            previous_update_id=None,
        )
        self.assertTrue(chunk.index.has_duplicates)
        self.assertEqual(chunk["update_id"].tolist(), [1, 2, 3])
        self.assertEqual(quality["duplicate_transaction_timestamps"], 1)
        self.assertEqual(quality["duplicate_update_ids"], 0)

    def test_equal_time_update_id_regression_is_rejected(self) -> None:
        raw = self._raw()
        raw.loc[1, "update_id"] = 0
        with self.assertRaisesRegex(BinanceDataError, "update id regressed"):
            _coerce_ordered_raw_chunk(
                raw,
                previous_transaction_ms=None,
                previous_update_id=None,
            )

    def test_cross_chunk_time_regression_is_rejected(self) -> None:
        raw = self._raw().iloc[[0]].copy()
        with self.assertRaisesRegex(BinanceDataError, "transaction time regressed"):
            _coerce_ordered_raw_chunk(
                raw,
                previous_transaction_ms=1_697_328_001_100,
                previous_update_id=0,
            )

    def test_malformed_crossed_and_nonpositive_rows_fail_closed(self) -> None:
        malformed = self._raw().copy()
        # pandas 3.x rejects lossy string assignment into float64 before the loader can inspect it;
        # use an object-typed fixture to model the raw CSV parser contract explicitly.
        malformed["best_bid_qty"] = malformed["best_bid_qty"].astype(object)
        malformed.at[0, "best_bid_qty"] = "bad"
        with self.assertRaisesRegex(BinanceDataError, "malformed numeric"):
            _coerce_ordered_raw_chunk(
                malformed,
                previous_transaction_ms=None,
                previous_update_id=None,
            )
        crossed = self._raw().copy()
        crossed.loc[0, "best_bid_price"] = 101.0
        with self.assertRaisesRegex(BinanceDataError, "crossed quote"):
            _coerce_ordered_raw_chunk(
                crossed,
                previous_transaction_ms=None,
                previous_update_id=None,
            )
        nonpositive = self._raw().copy()
        nonpositive.loc[0, "best_ask_qty"] = 0.0
        with self.assertRaisesRegex(BinanceDataError, "nonpositive"):
            _coerce_ordered_raw_chunk(
                nonpositive,
                previous_transaction_ms=None,
                previous_update_id=None,
            )


class RawOpenBucketCarryContracts(unittest.TestCase):
    @staticmethod
    def _ordered_states() -> pd.DataFrame:
        index = pd.DatetimeIndex(
            [
                "2023-10-15T00:00:01.000Z",
                "2023-10-15T00:00:01.000Z",
                "2023-10-15T00:00:09.900Z",
                "2023-10-15T00:00:10.100Z",
                "2023-10-15T00:00:15.000Z",
                "2023-10-15T00:00:19.999Z",
                "2023-10-15T00:00:20.001Z",
                "2023-10-15T00:00:21.000Z",
            ]
        )
        return pd.DataFrame(
            {
                "best_bid_price": [100.0, 100.0, 99.9, 99.9, 100.0, 100.0, 100.1, 100.1],
                "best_bid_qty": [10.0, 12.0, 9.0, 8.0, 6.0, 7.0, 5.0, 8.0],
                "best_ask_price": [100.1, 100.1, 100.1, 100.2, 100.2, 100.1, 100.2, 100.2],
                "best_ask_qty": [8.0, 7.0, 10.0, 9.0, 6.0, 5.0, 4.0, 7.0],
            },
            index=index,
        )

    def test_arbitrary_chunk_boundaries_match_single_pass_exactly(self) -> None:
        states = self._ordered_states()
        single, single_quality = aggregate_ordered_raw_quote_chunks([states])
        split, split_quality = aggregate_ordered_raw_quote_chunks(
            [
                states.iloc[:1],
                states.iloc[1:3],
                states.iloc[3:4],
                states.iloc[4:7],
                states.iloc[7:],
            ]
        )
        pd.testing.assert_frame_equal(single, split)
        self.assertEqual(single_quality["input_rows"], len(states.index))
        self.assertEqual(split_quality["emitted_raw_rows"], len(states.index))
        self.assertTrue(split_quality["raw_open_bucket_carry"])

    def test_duplicate_timestamp_events_contribute_individually(self) -> None:
        completed, _ = aggregate_ordered_raw_quote_chunks([self._ordered_states()])
        first = completed.loc[pd.Timestamp("2023-10-15T00:00:10Z")]
        self.assertEqual(float(first["quote_update_count"]), 3.0)
        self.assertEqual(float(first["bid_open"]), 100.0)
        self.assertEqual(float(first["bid_qty_open"]), 10.0)
        self.assertEqual(float(first["bid_close"]), 99.9)
        self.assertEqual(float(first["bid_qty_close"]), 9.0)

    def test_later_buckets_cannot_change_already_closed_history(self) -> None:
        states = self._ordered_states()
        base_result, _ = aggregate_ordered_raw_quote_chunks([states])
        future = pd.DataFrame(
            {
                "best_bid_price": [50.0, 200.0],
                "best_bid_qty": [1_000.0, 1.0],
                "best_ask_price": [50.1, 200.1],
                "best_ask_qty": [1.0, 1_000.0],
            },
            index=pd.DatetimeIndex(
                ["2023-10-15T00:00:31Z", "2023-10-15T00:00:41Z"]
            ),
        )
        extended, _ = aggregate_ordered_raw_quote_chunks([states, future])
        closed_before_extension = base_result.index[
            base_result.index <= pd.Timestamp("2023-10-15T00:00:20Z")
        ]
        pd.testing.assert_frame_equal(
            base_result.loc[closed_before_extension],
            extended.loc[closed_before_extension],
        )

    def test_streaming_revision_contains_no_daily_materialization_or_trading_logic(self) -> None:
        source = Path(__file__).with_name("quote_resiliency_data_v2.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            DATA_REVISION,
            "BINANCE_USDM_BOOKTICKER_COMPLETED_10S_V2_TRUE_STREAMING",
        )
        self.assertNotIn("list(iterator)", source)
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
