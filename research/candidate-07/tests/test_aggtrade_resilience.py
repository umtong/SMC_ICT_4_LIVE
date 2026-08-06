from __future__ import annotations

import csv
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np
import pandas as pd

from data_aggtrades_1s import _read_archive_to_seconds
from diagnose_aggtrade_resilience_v2 import preconsume_before_event_window
from diagnose_impact_resilience_1s import Pool
from run_aggtrade_resilience_second_safe import (
    first_touch_after_complete_confirmation_second,
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
