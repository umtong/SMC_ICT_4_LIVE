from __future__ import annotations

import csv
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np
import pandas as pd

from data_aggtrades_1s import (
    _complete_no_trade_seconds,
    _read_archive_to_seconds,
)
from diagnose_aggtrade_resilience_v2 import preconsume_before_event_window
from diagnose_impact_resilience_1s import Pool
from run_aggtrade_resilience_second_safe import (
    deduplicate_contact_pools_event_safe,
    first_touch_after_complete_confirmation_second,
    target_pool_after_complete_confirmation_second,
)


class AggTradeLoaderTests(unittest.TestCase):
    def _archive(self, rows: list[list[object]]) -> Path:
        temporary = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        temporary.close()
        path = Path(temporary.name)
        stream = io.StringIO(newline="")
        csv.writer(stream).writerows(rows)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("sample.csv", stream.getvalue().encode("utf-8"))
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_header_archive_reconciles_aggressor_quote_flow(self) -> None:
        path = self._archive(
            [
                [
                    "agg_trade_id", "price", "quantity", "first_trade_id",
                    "last_trade_id", "transact_time", "is_buyer_maker",
                ],
                [1, "100.0", "2.0", 10, 10, 1735689600000, "false"],
                [2, "101.0", "1.0", 11, 11, 1735689600500, "true"],
                [3, "102.0", "1.5", 12, 12, 1735689601000, "false"],
            ]
        )
        records, diagnostics = _read_archive_to_seconds(
            path,
            load_start_ns=1_700_000_000_000_000_000,
            trade_end_ns=1_800_000_000_000_000_000,
        )
        self.assertEqual(len(records), 2)
        first = records[0]
        self.assertEqual(first["open"], 100.0)
        self.assertEqual(first["high"], 101.0)
        self.assertEqual(first["close"], 101.0)
        self.assertAlmostEqual(first["quote_volume"], 301.0)
        self.assertAlmostEqual(first["taker_buy_quote"], 200.0)
        self.assertAlmostEqual(first["taker_sell_quote"], 101.0)
        self.assertEqual(first["trade_count"], 2)
        self.assertEqual(diagnostics["raw_rows"], 3)

    def test_headerless_archive_is_streamed_with_fixed_schema(self) -> None:
        path = self._archive(
            [
                [1, "100.0", "1.0", 10, 10, 1735689600000, "true"],
                [2, "99.5", "2.0", 11, 11, 1735689600100, "false"],
            ]
        )
        records, _ = _read_archive_to_seconds(
            path,
            load_start_ns=1_700_000_000_000_000_000,
            trade_end_ns=1_800_000_000_000_000_000,
        )
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["taker_sell_quote"], 100.0)
        self.assertAlmostEqual(records[0]["taker_buy_quote"], 199.0)

    def test_no_trade_second_is_causal_zero_flow_not_data_gap(self) -> None:
        base = 1_735_689_600_000_000_000
        records = pd.DataFrame(
            [
                {
                    "timestamp_ns": base + 999_999_999,
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1.0,
                    "quote_volume": 100.0,
                    "taker_buy_quote": 100.0,
                    "taker_sell_quote": 0.0,
                    "trade_count": 1,
                    "first_trade_ns": base + 100,
                    "last_trade_ns": base + 100,
                },
                {
                    "timestamp_ns": base + 2_999_999_999,
                    "open": 102.0,
                    "high": 102.0,
                    "low": 102.0,
                    "close": 102.0,
                    "volume": 2.0,
                    "quote_volume": 204.0,
                    "taker_buy_quote": 0.0,
                    "taker_sell_quote": 204.0,
                    "trade_count": 1,
                    "first_trade_ns": base + 2_000_000_100,
                    "last_trade_ns": base + 2_000_000_100,
                },
            ]
        )
        completed, diagnostics = _complete_no_trade_seconds(
            records,
            load_start_ns=base,
            trade_end_ns=base + 3_000_000_000,
        )
        self.assertEqual(len(completed.index), 3)
        middle = completed.iloc[1]
        self.assertFalse(bool(middle["had_trade"]))
        self.assertEqual(int(middle["trade_count"]), 0)
        self.assertEqual(float(middle["open"]), 100.0)
        self.assertEqual(float(middle["high"]), 100.0)
        self.assertEqual(float(middle["low"]), 100.0)
        self.assertEqual(float(middle["close"]), 100.0)
        self.assertEqual(float(middle["quote_volume"]), 0.0)
        self.assertEqual(float(middle["taker_buy_quote"]), 0.0)
        self.assertEqual(float(middle["taker_sell_quote"]), 0.0)
        self.assertEqual(int(middle["first_trade_ns"]), -1)
        self.assertEqual(diagnostics["causal_zero_flow_seconds"], 1)
        self.assertTrue(
            bool(
                (
                    completed["timestamp_ns"].diff().dropna()
                    == 1_000_000_000
                ).all()
            )
        )


class CausalSecondBoundaryTests(unittest.TestCase):
    def test_confirmation_second_is_not_a_post_confirmation_touch(self) -> None:
        second = 1_766_103_599
        timestamps = np.array(
            [
                second * 1_000_000_000 + 999_999_999,
                (second + 1) * 1_000_000_000 + 999_999_999,
            ],
            dtype=np.int64,
        )
        previous_close = np.array([99.0, 99.0])
        highs = np.array([101.0, 101.0])
        lows = np.array([98.0, 98.0])
        pool = Pool(
            "5MH-causal",
            "5M",
            "UPPER",
            100.0,
            0,
            second * 1_000_000_000 + 999_000_000,
        )
        touch = first_touch_after_complete_confirmation_second(
            pool,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
        )
        self.assertEqual(touch, 1)

    def test_target_confirmed_in_entry_second_is_not_eligible(self) -> None:
        second = 1_766_103_599
        timestamps = np.array(
            [
                second * 1_000_000_000 + 999_999_999,
                (second + 1) * 1_000_000_000 + 999_999_999,
            ],
            dtype=np.int64,
        )
        same_second = Pool(
            "1MH-same-second",
            "1M",
            "UPPER",
            102.0,
            0,
            second * 1_000_000_000 + 999_000_000,
        )
        selected = target_pool_after_complete_confirmation_second(
            {"1M": [same_second], "5M": []},
            direction="LONG",
            entry=100.0,
            stop=99.0,
            entry_index=0,
            timestamps=timestamps,
            previous_close=np.array([100.0, 100.0]),
            highs=np.array([100.5, 100.5]),
            lows=np.array([99.5, 99.5]),
            touch_cache={},
            minimum_rr=1.25,
        )
        self.assertIsNone(selected)


class EventExclusivityTests(unittest.TestCase):
    def test_second_pool_inside_first_observation_window_is_consumed(self) -> None:
        start_second = 1_766_103_600
        timestamps = np.array(
            [
                (start_second + index) * 1_000_000_000 + 999_999_999
                for index in range(30)
            ],
            dtype=np.int64,
        )
        highs = np.full(30, 99.5)
        highs[2] = 100.5
        highs[5] = 101.5
        bars = pd.DataFrame(
            {
                "timestamp_ns": timestamps,
                "open": np.full(30, 99.0),
                "high": highs,
                "low": np.full(30, 98.0),
                "close": np.full(30, 99.0),
            }
        )
        confirmation = (start_second - 2) * 1_000_000_000 + 999_000_000
        first = Pool("5MH-first", "5M", "UPPER", 100.0, 0, confirmation)
        second = Pool("5MH-second", "5M", "UPPER", 101.0, 0, confirmation)
        selected, summary = deduplicate_contact_pools_event_safe(
            bars,
            [first, second],
        )
        self.assertEqual([pool.pool_id for pool in selected], ["5MH-first"])
        self.assertEqual(summary["pools_consumed_inside_prior_event"], 1)


class PreconsumptionTests(unittest.TestCase):
    def test_old_pool_touched_before_raw_window_is_removed(self) -> None:
        minute = pd.DataFrame(
            {
                "timestamp_ns": [100, 200, 300, 400],
                "high": [99.0, 101.0, 99.5, 99.0],
                "low": [95.0, 96.0, 95.5, 95.0],
            }
        )
        touched = Pool("5MH-1", "5M", "UPPER", 100.0, 0, 50)
        untouched = Pool("5ML-2", "5M", "LOWER", 94.0, 0, 50)
        new = Pool("5MH-3", "5M", "UPPER", 110.0, 300, 350)
        retained, summary = preconsume_before_event_window(
            [touched, untouched, new],
            minute,
            event_start_ns=350,
        )
        self.assertEqual({pool.pool_id for pool in retained}, {"5ML-2", "5MH-3"})
        self.assertEqual(summary["pre_event_consumed"], 1)


if __name__ == "__main__":
    unittest.main()
