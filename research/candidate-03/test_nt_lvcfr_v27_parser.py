from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from derive_nt_lvcfr_v27_signals import read_kline_archive


class HeaderSafeParserTests(unittest.TestCase):
    def test_header_row_is_ignored_without_dtype_mutation(self) -> None:
        directory = Path(tempfile.mkdtemp())
        path = directory / "header.zip"
        header = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "count", "taker_buy_volume",
            "taker_buy_quote_volume", "ignore",
        ]
        row = [
            1_704_067_200_000, 100.0, 101.0, 99.0, 100.5, 1.0,
            1_704_067_259_999, 100.0, 10, 0.6, 60.0, 0,
        ]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "sample.csv",
                ",".join(header) + "\n" + ",".join(map(str, row)) + "\n",
            )
        frame = read_kline_archive(path, "futures")
        self.assertEqual(len(frame), 1)
        self.assertEqual(int(frame.iloc[0].open_time_ms), 1_704_067_200_000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
