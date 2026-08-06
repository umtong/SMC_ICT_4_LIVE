#!/usr/bin/env python3
"""Probe official Binance USD-M derivatives archives for candidate 10.

This is a data-contract probe only. It downloads checksum-protected archives,
records the observed schemas and row counts, and does not calculate PnL or
simulate execution.
"""
from __future__ import annotations

import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import zipfile

DATE = "2023-10-16"
SYMBOL = "BTCUSDT"
ROOT = Path("artifacts/candidate-10-derivatives-data-probe")
DATA = Path("/tmp/candidate-10-derivatives-data-probe")
BASE = "https://data.binance.vision/data/futures/um/daily"
DATASETS = (
    "metrics",
    "liquidationSnapshot",
    "fundingRate",
    "bookDepth",
)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, path: Path) -> None:
    if path.exists():
        return
    with urlopen(url, timeout=180) as response, path.open("wb") as out:
        shutil.copyfileobj(response, out, length=8 * 1024 * 1024)


def _inspect_dataset(dataset: str) -> dict[str, Any]:
    stem = f"{SYMBOL}-{dataset}-{DATE}"
    url = f"{BASE}/{dataset}/{SYMBOL}/{stem}.zip"
    archive = DATA / f"{stem}.zip"
    checksum = DATA / f"{stem}.zip.CHECKSUM"
    result: dict[str, Any] = {
        "dataset": dataset,
        "url": url,
        "available": False,
    }
    try:
        _download(url + ".CHECKSUM", checksum)
        _download(url, archive)
    except HTTPError as exc:
        result.update({"http_status": exc.code, "error": str(exc)})
        return result
    except (URLError, TimeoutError) as exc:
        result["error"] = repr(exc)
        return result

    expected = checksum.read_text(encoding="utf-8").strip().split()[0]
    actual = _sha256_file(archive)
    if expected.lower() != actual.lower():
        raise RuntimeError(f"checksum mismatch for {archive.name}: {actual} != {expected}")

    widths: dict[str, int] = {}
    header: list[str] | None = None
    first_rows: list[list[str]] = []
    last_rows: list[list[str]] = []
    row_count = 0
    with zipfile.ZipFile(archive) as zf:
        members = [name for name in zf.namelist() if name.endswith(".csv")]
        if len(members) != 1:
            raise RuntimeError(f"expected one CSV in {archive.name}, got {members}")
        info = zf.getinfo(members[0])
        with io.TextIOWrapper(zf.open(members[0]), encoding="utf-8") as stream:
            for raw in csv.reader(stream):
                if not raw:
                    continue
                row = [item.strip() for item in raw]
                if row_count == 0 and not row[0].lstrip("-").replace(".", "", 1).isdigit():
                    header = row
                    continue
                row_count += 1
                widths[str(len(row))] = widths.get(str(len(row)), 0) + 1
                if len(first_rows) < 5:
                    first_rows.append(row)
                last_rows.append(row)
                if len(last_rows) > 5:
                    last_rows.pop(0)

    result.update(
        {
            "available": True,
            "sha256": actual,
            "archive_bytes": archive.stat().st_size,
            "csv_member": members[0],
            "csv_uncompressed_bytes": info.file_size,
            "header": header,
            "row_count": row_count,
            "width_counts": widths,
            "first_rows": first_rows,
            "last_rows": last_rows,
        },
    )
    return result


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    datasets = [_inspect_dataset(name) for name in DATASETS]
    report = {
        "date": DATE,
        "symbol": SYMBOL,
        "datasets": datasets,
        "elapsed_seconds": time.perf_counter() - started,
        "purpose": "official data-contract probe only; no strategy or PnL claim",
    }
    (ROOT / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
