#!/usr/bin/env python3
from __future__ import annotations

import csv
import tempfile
from pathlib import Path
import zipfile

from open_interest_metrics_data import _read_archive

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    archive = root / "sample.zip"
    csv_path = root / "sample.csv"
    rows = [
        [
            "create_time",
            "symbol",
            "sum_open_interest",
            "sum_open_interest_value",
            "count_toptrader_long_short_ratio",
            "sum_toptrader_long_short_ratio",
            "count_long_short_ratio",
            "sum_taker_long_short_vol_ratio",
        ],
        ["2024-02-26 00:00:00", "BTCUSDT", "1000", "50000000", "1", "1", "1", "1.1"],
        ["2024-02-26 00:05:00", "BTCUSDT", "990", "49500000", "1", "1", "1", "0.9"],
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(csv_path, arcname="sample.csv")
    points = _read_archive(archive)
    assert len(points) == 2
    assert points[0].open_interest == 1000.0
    assert points[1].open_interest_value == 49_500_000.0
    assert points[1].ts_ns > points[0].ts_ns
print("open-interest metrics parser contract passed")
