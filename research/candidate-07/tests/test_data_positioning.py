from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from data_positioning import _read_metrics_archive


HEADER = (
    "create_time,symbol,sum_open_interest,sum_open_interest_value,"
    "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
    "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
)


class PositioningArchiveTest(unittest.TestCase):
    def test_reads_required_metrics_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "BTCUSDT-metrics.csv",
                    HEADER
                    + "2025-12-22 00:05:00,BTCUSDT,89863.431,7973001584.62,"
                    "2.02,2.25,1.70,1.10\n",
                )
            rows = _read_metrics_archive(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], "BTCUSDT")
            self.assertEqual(rows[0]["sum_open_interest"], "89863.431")

    def test_rejects_missing_open_interest_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "bad.csv",
                    "create_time,symbol,sum_open_interest_value\n"
                    "2025-12-22 00:05:00,BTCUSDT,1\n",
                )
            with self.assertRaises(RuntimeError):
                _read_metrics_archive(path)


if __name__ == "__main__":
    unittest.main()
