from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from chunk_assembly import ChunkAssemblyError, assemble_universe


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write_chunk(root: Path, symbol: str, stamp: str) -> None:
    day = date.fromisoformat(stamp)
    directory = root / symbol / stamp
    directory.mkdir(parents=True)
    grid = pd.date_range(pd.Timestamp(day, tz="UTC"), periods=1440, freq="1min")
    close = grid + pd.Timedelta(seconds=59, milliseconds=999)
    klines = pd.DataFrame(
        {
            "open_time_dt": grid,
            "close_time_dt": close,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1.0,
        },
    )
    features = pd.DataFrame(
        {
            "observed_time_ns": close.astype("int64"),
            "feature_ready": True,
            "flow_60s": 0.1,
        },
    )
    kline_path = directory / "klines.csv.gz"
    feature_path = directory / "features.csv.gz"
    klines.to_csv(kline_path, index=False, compression="gzip")
    features.to_csv(feature_path, index=False, compression="gzip")
    manifest = {
        "symbol": symbol,
        "core_start": stamp,
        "core_end": stamp,
        "rows": 1440,
        "files": {
            "klines.csv.gz": {"sha256": digest(kline_path)},
            "features.csv.gz": {"sha256": digest(feature_path)},
        },
    }
    (directory / "chunk_manifest.json").write_text(json.dumps(manifest))


class ChunkAssemblyTest(unittest.TestCase):
    def test_four_symbol_continuous_assembly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
                write_chunk(root, symbol, "2026-01-01")
                write_chunk(root, symbol, "2026-01-02")
            frames, paths, manifest = assemble_universe(
                input_root=root,
                start=date(2026, 1, 1),
                end=date(2026, 1, 2),
                workspace=root / "workspace",
            )
            self.assertEqual(
                {key: len(value) for key, value in frames.items()},
                {symbol: 2880 for symbol in frames},
            )
            self.assertTrue(all(path.is_file() for path in paths.values()))
            self.assertEqual(manifest["minute_rows_per_symbol"], 2880)
            self.assertTrue(manifest["single_continuous_account"])

    def test_gap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
                write_chunk(root, symbol, "2026-01-01")
            with self.assertRaises(ChunkAssemblyError):
                assemble_universe(
                    input_root=root,
                    start=date(2026, 1, 1),
                    end=date(2026, 1, 2),
                    workspace=root / "workspace",
                )


if __name__ == "__main__":
    unittest.main()
