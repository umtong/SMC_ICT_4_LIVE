"""Download and validate one official bookDepth/metrics day for v0.2."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from typing import Any

from aggtrade_probe import BASE, csv_text, download, verify_checksum


def rows_from_zip(payload: bytes, source: str) -> tuple[list[str], list[list[str]]]:
    text = csv_text(payload, source)
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if row]
    if not rows:
        raise ValueError(f"{source}: no rows")
    header = rows[0]
    if all(cell.replace("_", "").isalpha() for cell in header):
        return header, rows[1:]
    return [f"column_{index}" for index in range(len(header))], rows


def inspect(data_type: str, symbol: str, day: str) -> dict[str, Any]:
    filename = f"{symbol}-{data_type}-{day}.zip"
    url = f"{BASE}/{data_type}/{symbol}/{filename}"
    payload = download(url)
    checksum = verify_checksum(payload, download(f"{url}.CHECKSUM"), filename)
    header, rows = rows_from_zip(payload, filename)
    widths = sorted({len(row) for row in rows})
    if widths != [len(header)]:
        raise ValueError(f"{filename}: inconsistent row widths {widths}, header={len(header)}")
    return {
        "data_type": data_type,
        "url": url,
        "sha256": checksum,
        "zip_bytes": len(payload),
        "columns": header,
        "row_count": len(rows),
        "first_rows": rows[:3],
        "last_rows": rows[-3:],
    }


def main() -> int:
    symbol = "BTCUSDT"
    day = "2024-10-14"
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "date": day,
        "datasets": [inspect("bookDepth", symbol, day), inspect("metrics", symbol, day)],
    }
    output = Path("artifacts/candidate-09-context-sample")
    output.mkdir(parents=True, exist_ok=True)
    (output / "sample.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
