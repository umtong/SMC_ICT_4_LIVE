"""Pure contracts for checksum-derived native L1 completion snapshots."""

from __future__ import annotations

import unittest

import pandas as pd

from quote_resiliency_native_quotes import (
    COMPLETION_DELAY_NS,
    NATIVE_QUOTE_REVISION,
    completion_quote_ticks_from_frame,
)
from run_aggtrade_acceptance_nautilus import _build_instrument


class NativeQuoteSnapshotContracts(unittest.TestCase):
    @staticmethod
    def _instrument():
        return _build_instrument(
            "BTCUSDT",
            {
                "instrument_id": "BTCUSDT-PERP.BINANCE",
                "base_currency": "BTC",
                "price_precision": 1,
                "size_precision": 3,
                "tick_size": "0.1",
                "size_increment": "0.001",
                "min_quantity": "0.001",
            },
            0.0006,
        )

    @staticmethod
    def _frame() -> pd.DataFrame:
        bucket_end = pd.Timestamp("2023-10-15T00:00:10Z")
        source_event = pd.Timestamp("2023-10-15T00:00:09.999Z")
        return pd.DataFrame(
            {
                "bid_close": [99.9],
                "ask_close": [100.1],
                "bid_qty_close": [12.345],
                "ask_qty_close": [6.789],
                "quote_last_event_ns": [int(source_event.as_unit("ns").value)],
                "native_quote_snapshot_observable": [True],
            },
            index=pd.DatetimeIndex([bucket_end]),
        )

    def test_completed_snapshot_reproduces_venue_state_at_bar_end_plus_one_ns(self) -> None:
        frame = self._frame()
        ticks, quality = completion_quote_ticks_from_frame(
            frame,
            instrument=self._instrument(),
        )
        self.assertEqual(len(ticks), 1)
        tick = ticks[0]
        bucket_end_ns = int(frame.index[0].as_unit("ns").value)
        self.assertEqual(int(tick.ts_event), bucket_end_ns + COMPLETION_DELAY_NS)
        self.assertEqual(int(tick.ts_init), bucket_end_ns + COMPLETION_DELAY_NS)
        self.assertEqual(float(tick.bid_price.as_double()), 99.9)
        self.assertEqual(float(tick.ask_price.as_double()), 100.1)
        self.assertEqual(float(tick.bid_size.as_double()), 12.345)
        self.assertEqual(float(tick.ask_size.as_double()), 6.789)
        self.assertEqual(quality.revision, NATIVE_QUOTE_REVISION)
        self.assertEqual(quality.rows, 1)
        self.assertEqual(quality.maximum_source_age_ns, 1_000_000)
        # The low-level pyo3 engine consumes the same canonical quote object.
        self.assertEqual(int(tick.to_pyo3().ts_event), int(tick.ts_event))

    def test_unobservable_bucket_emits_no_native_quote(self) -> None:
        frame = self._frame()
        frame["native_quote_snapshot_observable"] = False
        ticks, quality = completion_quote_ticks_from_frame(
            frame,
            instrument=self._instrument(),
        )
        self.assertEqual(ticks, [])
        self.assertEqual(quality.rows, 0)

    def test_non_quote_frame_is_backward_compatible(self) -> None:
        frame = pd.DataFrame(
            {"close": [100.0]},
            index=pd.DatetimeIndex(["2023-10-15T00:00:10Z"]),
        )
        ticks, quality = completion_quote_ticks_from_frame(
            frame,
            instrument=self._instrument(),
        )
        self.assertEqual(ticks, [])
        self.assertEqual(quality.source_contract, "NO_NATIVE_QUOTE_COLUMNS")

    def test_source_event_must_be_inside_the_completed_bucket(self) -> None:
        frame = self._frame()
        bucket_end_ns = int(frame.index[0].as_unit("ns").value)
        for invalid_source in (
            bucket_end_ns,
            bucket_end_ns + 1,
            bucket_end_ns - 10_000_000_000,
        ):
            broken = frame.copy()
            broken["quote_last_event_ns"] = invalid_source
            with self.assertRaisesRegex(ValueError, "strictly before"):
                completion_quote_ticks_from_frame(
                    broken,
                    instrument=self._instrument(),
                )

    def test_invalid_or_crossed_state_fails_closed(self) -> None:
        for column, value in (
            ("bid_close", 0.0),
            ("ask_close", float("nan")),
            ("bid_qty_close", -1.0),
            ("ask_qty_close", 0.0),
        ):
            broken = self._frame()
            broken[column] = value
            with self.assertRaises(ValueError):
                completion_quote_ticks_from_frame(
                    broken,
                    instrument=self._instrument(),
                )
        crossed = self._frame()
        crossed["bid_close"] = 100.2
        with self.assertRaisesRegex(ValueError, "crossed"):
            completion_quote_ticks_from_frame(
                crossed,
                instrument=self._instrument(),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
