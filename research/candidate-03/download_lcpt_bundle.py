#!/usr/bin/env python3
"""Download one checksum-verified LCPT BTC week plus one warm-up day."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen, urlretrieve


SOURCES = {
    "futures-agg": (
        "BINANCE_USD_M_FUTURES",
        "https://data.binance.vision/data/futures/um/daily/aggTrades/{symbol}/"
        "{symbol}-aggTrades-{day}.zip",
    ),
    "spot-agg": (
        "BINANCE_SPOT",
        "https://data.binance.vision/data/spot/daily/aggTrades/{symbol}/"
        "{symbol}-aggTrades-{day}.zip",
    ),
    "metrics": (
        "BINANCE_USD_M_FUTURES",
        "https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/"
        "{symbol}-metrics-{day}.zip",
    ),
}


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_text(url: str, attempts: int = 4) -> str:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(url, timeout=90) as response:
                return response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed: {url}") from last_error


def fetch_file(url: str, destination: Path, attempts: int = 4) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            temporary = destination.with_suffix(destination.suffix + ".part")
            temporary.unlink(missing_ok=True)
            urlretrieve(url, temporary)
            temporary.replace(destination)
            return
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            destination.with_suffix(destination.suffix + ".part").unlink(missing_ok=True)
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed: {url}") from last_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    symbol = args.symbol.upper()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    first_day = args.week_start - timedelta(days=1)
    last_day = args.week_start + timedelta(days=6)
    manifest: dict[str, object] = {
        "symbol": symbol,
        "week_start": args.week_start.isoformat(),
        "warmup_start": first_day.isoformat(),
        "last_included_day": last_day.isoformat(),
        "source": "Binance Vision public data",
        "datasets": {},
    }

    for dataset, (venue, template) in SOURCES.items():
        destination_dir = output / dataset
        destination_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, object]] = []
        day = first_day
        while day <= last_day:
            stamp = day.isoformat()
            url = template.format(symbol=symbol, day=stamp)
            name = url.rsplit("/", 1)[-1]
            destination = destination_dir / name
            expected = fetch_text(url + ".CHECKSUM").strip().split()[0].lower()
            if destination.is_file() and sha256_file(destination).lower() == expected:
                actual = expected
            else:
                destination.unlink(missing_ok=True)
                fetch_file(url, destination)
                actual = sha256_file(destination).lower()
                if actual != expected:
                    destination.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"checksum mismatch for {name}: {actual} != {expected}",
                    )
            records.append(
                {
                    "date": stamp,
                    "venue": venue,
                    "dataset": dataset,
                    "archive": destination.as_posix(),
                    "archive_size_bytes": destination.stat().st_size,
                    "sha256": actual,
                    "source_url": url,
                },
            )
            print(destination)
            day += timedelta(days=1)
        manifest["datasets"][dataset] = records

    manifest_path = output / (
        f"{symbol}-lcpt-{args.week_start}-{last_day}.manifest.json"
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
