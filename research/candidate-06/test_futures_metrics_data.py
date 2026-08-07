from __future__ import annotations

import csv
import io
from pathlib import Path
import tempfile
import zipfile

from futures_metrics_data import _read_archive, _timestamp_ns


def main() -> None:
    assert _timestamp_ns("1708905600") == 1708905600 * 1_000_000_000
    assert _timestamp_ns("1708905600000") == 1708905600000 * 1_000_000
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sample.zip"
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "create_time",
            "symbol",
            "sum_open_interest",
            "sum_open_interest_value",
            "count_toptrader_long_short_ratio",
            "sum_toptrader_long_short_ratio",
            "count_long_short_ratio",
            "sum_taker_long_short_vol_ratio",
        ])
        writer.writerow(["1708905600", "BTCUSDT", "100", "5000000", "1.1", "1.2", "1.0", "0.5"])
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("sample.csv", buffer.getvalue())
        rows = _read_archive(path)
        assert len(rows) == 1
        assert rows[0].open_interest == 100.0
        assert abs(rows[0].signed_taker_ratio + 1.0 / 3.0) < 1e-12
    print("futures metrics parser contract passed")


if __name__ == "__main__":
    main()
