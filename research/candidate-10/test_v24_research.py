"""Data-time regression tests for candidate-10 v24."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from c10_v24_research import (
    _AggBucket,
    aggregate_aggtrade_archives,
    align_cross_market_rows,
)


def _archive(root: Path, name: str, rows: list[str]) -> Path:
    path = root / f"{name}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{name}.csv", "\n".join(rows) + "\n")
    return path


class V24ResearchDataTests(unittest.TestCase):
    def test_exact_boundary_trade_belongs_to_next_completed_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = _archive(
                root,
                "BTCUSDT-aggTrades-1970-01-01",
                [
                    "1,100.0,1.0,1,1,0,false,true",
                    "2,101.0,1.0,2,2,4999,true,true",
                    "3,102.0,1.0,3,3,5000,false,true",
                ],
            )
            buckets, quality = aggregate_aggtrade_archives(
                [path],
                bucket_seconds=5,
                market="SPOT",
            )
            self.assertEqual(
                sorted(buckets),
                [5_000_000_000, 10_000_000_000],
            )
            first = buckets[5_000_000_000]
            second = buckets[10_000_000_000]
            self.assertEqual(first.trade_count, 2)
            self.assertEqual(first.close, 101.0)
            self.assertEqual(first.last_trade_ts_ns, 4_999_000_000)
            self.assertEqual(second.trade_count, 1)
            self.assertEqual(second.open, 102.0)
            self.assertEqual(second.first_trade_ts_ns, 5_000_000_000)
            self.assertEqual(quality["duplicate_id_count"], 0)
            self.assertEqual(quality["nonmonotonic_timestamp_count"], 0)

    def test_alignment_uses_only_common_completed_buckets(self) -> None:
        spot = {
            5_000_000_000: _AggBucket(
                100.0,
                101.0,
                99.0,
                100.5,
                1_000.0,
                600.0,
                10,
                1,
                4_000_000_000,
            ),
            10_000_000_000: _AggBucket(
                100.5,
                102.0,
                100.0,
                101.0,
                2_000.0,
                900.0,
                12,
                5_000_000_000,
                9_000_000_000,
            ),
        }
        perp = {
            5_000_000_000: _AggBucket(
                100.1,
                101.1,
                99.1,
                100.6,
                1_100.0,
                650.0,
                11,
                1,
                4_500_000_000,
            ),
        }
        rows, quality = align_cross_market_rows(
            spot,
            perp,
            bucket_seconds=5,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ts_ns"], 5_000_000_000)
        self.assertLess(rows[0]["spot_last_trade_ts_ns"], rows[0]["ts_ns"])
        self.assertLess(rows[0]["perp_last_trade_ts_ns"], rows[0]["ts_ns"])
        self.assertEqual(quality["spot_only_bucket_count"], 1)
        self.assertEqual(quality["perp_only_bucket_count"], 0)
        self.assertEqual(quality["gap_count"], 0)

    def test_duplicate_aggregate_trade_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = _archive(
                root,
                "BTCUSDT-aggTrades-1970-01-01",
                [
                    "1,100.0,1.0,1,1,0,false,true",
                    "1,101.0,1.0,2,2,1,true,true",
                ],
            )
            with self.assertRaisesRegex(RuntimeError, "duplicate=1"):
                aggregate_aggtrade_archives(
                    [path],
                    bucket_seconds=5,
                    market="SPOT",
                )


if __name__ == "__main__":
    unittest.main()
