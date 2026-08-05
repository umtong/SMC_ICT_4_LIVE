#!/usr/bin/env python3
"""Download checksum-verified Binance Vision USD-M daily archives."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.request import urlopen, urlretrieve
import zipfile

BASE = "https://data.binance.vision/data/futures/um/daily"
SUPPORTED_DATASETS = {"aggTrades", "bookTicker", "metrics", "trades", "klines"}


def dates(start: date, end: date) -> Iterator[date]:
    if end < start:
        raise ValueError("end date precedes start date")
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: zipfile.ZipFile, destination: Path) -> list[str]:
    extracted: list[str] = []
    root = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if root not in target.parents and target != root:
            raise RuntimeError(f"unsafe archive member: {member.filename}")
        archive.extract(member, destination)
        if not member.is_dir():
            extracted.append(member.filename)
    return extracted


def fetch_text(url: str, attempts: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(url, timeout=60) as response:
                return response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed: {url}") from last_error


def fetch_file(url: str, destination: Path, attempts: int = 3) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            urlretrieve(url, destination)
            return
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed: {url}") from last_error


def archive_name(symbol: str, dataset: str, day: date, interval: str | None) -> str:
    stamp = day.isoformat()
    if dataset == "klines":
        if not interval:
            raise ValueError("--interval is required for klines")
        return f"{symbol}-{interval}-{stamp}.zip"
    return f"{symbol}-{dataset}-{stamp}.zip"


def archive_url(symbol: str, dataset: str, name: str, interval: str | None) -> str:
    if dataset == "klines":
        assert interval is not None
        return f"{BASE}/klines/{symbol}/{interval}/{name}"
    return f"{BASE}/{dataset}/{symbol}/{name}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--dataset", choices=sorted(SUPPORTED_DATASETS), default="aggTrades")
    parser.add_argument("--interval")
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-extract", action="store_true")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []

    for day in dates(args.start_date, args.end_date):
        name = archive_name(symbol, args.dataset, day, args.interval)
        url = archive_url(symbol, args.dataset, name, args.interval)
        destination = output / name
        fetch_file(url, destination)
        expected = fetch_text(url + ".CHECKSUM").strip().split()[0].lower()
        actual = sha256_file(destination).lower()
        if actual != expected:
            destination.unlink(missing_ok=True)
            raise RuntimeError(f"checksum mismatch for {name}: {actual} != {expected}")
        extracted: list[str] = []
        if not args.no_extract:
            with zipfile.ZipFile(destination) as archive:
                extracted = safe_extract(archive, output)
        records.append(
            {
                "date": day.isoformat(),
                "dataset": args.dataset,
                "symbol": symbol,
                "archive": name,
                "archive_size_bytes": destination.stat().st_size,
                "sha256": actual,
                "source_url": url,
                "extracted": extracted,
            }
        )
        print(destination)

    manifest = {
        "venue": "BINANCE_USD_M_FUTURES",
        "source": "Binance Vision public data",
        "dataset": args.dataset,
        "symbol": symbol,
        "interval": args.interval,
        "start_date": args.start_date.isoformat(),
        "end_date": args.end_date.isoformat(),
        "records": records,
    }
    manifest_path = output / f"{symbol}-{args.dataset}-{args.start_date}-{args.end_date}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
