"""Deterministic Binance Vision downloader and normalizer.

Only public historical UM futures klines are fetched.  The normalized CSV preserves
the completed-bar fields required by the causal engine, including taker-buy volume.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import random
import time
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


BINANCE_VISION = "https://data.binance.vision/data/futures/um/daily/klines"
NORMALIZED_FIELDS = [
    "open_time_ns",
    "close_time_ns",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "taker_buy_volume",
    "taker_buy_quote_volume",
]


def _timestamp_ns(raw: str) -> int:
    value = int(raw)
    absolute = abs(value)
    if absolute >= 100_000_000_000_000_000:  # already nanoseconds
        return value
    if absolute >= 100_000_000_000_000:  # microseconds
        return value * 1_000
    if absolute >= 100_000_000_000:  # milliseconds
        return value * 1_000_000
    if absolute >= 1_000_000_000:  # seconds
        return value * 1_000_000_000
    raise ValueError(f"unrecognized timestamp scale: {raw}")


def selected_weeks_from_seed(selection: Mapping[str, Any]) -> list[str]:
    start = date.fromisoformat(str(selection["population_start_monday"]))
    end = date.fromisoformat(str(selection["population_end_monday"]))
    if start.weekday() != 0 or end.weekday() != 0:
        raise ValueError("selection population endpoints must be Mondays")
    mondays: list[str] = []
    current = start
    while current <= end:
        mondays.append(current.isoformat())
        current += timedelta(days=7)
    return random.Random(int(selection["seed"])).sample(mondays, 3)


def validate_frozen_selection(config: Mapping[str, Any]) -> None:
    expected = selected_weeks_from_seed(config["selection"])
    actual = [str(item["start"]) for item in config["weeks"]]
    if actual != expected:
        raise ValueError(
            "frozen week selection does not match recorded deterministic algorithm: "
            f"expected={expected}, actual={actual}"
        )


def _download_bytes(url: str, *, attempts: int = 4) -> bytes:
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-09/0.1"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=45) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if isinstance(exc, HTTPError) and exc.code == 404:
                raise FileNotFoundError(f"Binance Vision file not found: {url}") from exc
            if attempt + 1 < attempts:
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def _parse_archive(payload: bytes, *, source_name: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"{source_name}: expected one CSV in archive, got {names}")
        text = archive.read(names[0]).decode("utf-8")
    rows: list[dict[str, Any]] = []
    reader = csv.reader(io.StringIO(text))
    for raw in reader:
        if not raw:
            continue
        if not raw[0].lstrip("-").isdigit():
            # Some newer archives include a header.
            continue
        if len(raw) < 11:
            raise ValueError(f"{source_name}: malformed kline row with {len(raw)} columns")
        open_ns = _timestamp_ns(raw[0])
        close_ns = _timestamp_ns(raw[6])
        if close_ns <= open_ns:
            raise ValueError(f"{source_name}: close timestamp does not follow open timestamp")
        rows.append(
            {
                "open_time_ns": open_ns,
                "close_time_ns": close_ns,
                "open": raw[1],
                "high": raw[2],
                "low": raw[3],
                "close": raw[4],
                "volume": raw[5],
                "quote_volume": raw[7],
                "trades": int(raw[8]),
                "taker_buy_volume": raw[9],
                "taker_buy_quote_volume": raw[10],
            }
        )
    if not rows:
        raise ValueError(f"{source_name}: archive yielded no data rows")
    return rows


def _validate_rows(rows: Iterable[Mapping[str, Any]], *, expected_days: int) -> list[dict[str, Any]]:
    materialized = [dict(row) for row in rows]
    expected = 1440 * expected_days
    if len(materialized) != expected:
        raise ValueError(f"expected {expected} one-minute bars, got {len(materialized)}")
    last_close = -1
    for index, row in enumerate(materialized):
        close_ns = int(row["close_time_ns"])
        if close_ns <= last_close:
            raise ValueError(f"non-increasing close timestamp at row {index}")
        if last_close >= 0:
            gap = close_ns - last_close
            if gap != 60_000_000_000:
                raise ValueError(f"unexpected one-minute gap at row {index}: {gap} ns")
        last_close = close_ns
        volume = float(row["volume"])
        taker_buy = float(row["taker_buy_volume"])
        if volume < 0.0 or not 0.0 <= taker_buy <= volume + 1e-9:
            raise ValueError(f"invalid volume fields at row {index}")
    return materialized


def download_week(
    *,
    symbol: str,
    interval: str,
    start_date: str,
    days: int,
    output_dir: str | Path,
) -> Path:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(start_date)
    end = start + timedelta(days=days - 1)
    merged = destination / f"{symbol}-{interval}-{start.isoformat()}_{end.isoformat()}.csv"
    metadata = merged.with_suffix(".metadata.json")

    if merged.exists() and metadata.exists():
        with merged.open("r", encoding="utf-8", newline="") as stream:
            cached = list(csv.DictReader(stream))
        _validate_rows(cached, expected_days=days)
        return merged

    all_rows: list[dict[str, Any]] = []
    source_urls: list[str] = []
    for offset in range(days):
        current = start + timedelta(days=offset)
        filename = f"{symbol}-{interval}-{current.isoformat()}.zip"
        url = f"{BINANCE_VISION}/{symbol}/{interval}/{filename}"
        payload = _download_bytes(url)
        rows = _parse_archive(payload, source_name=filename)
        if len(rows) != 1440:
            raise ValueError(f"{filename}: expected 1440 bars, got {len(rows)}")
        all_rows.extend(rows)
        source_urls.append(url)

    validated = _validate_rows(all_rows, expected_days=days)
    temporary = merged.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=NORMALIZED_FIELDS)
        writer.writeheader()
        writer.writerows(validated)
    temporary.replace(merged)

    metadata.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "interval": interval,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "days": days,
                "rows": len(validated),
                "sources": source_urls,
                "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
                "timestamp_contract": "ts_init == completed kline close_time_ns",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return merged
