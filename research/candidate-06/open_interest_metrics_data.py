"""Checksum-verified Binance USD-M open-interest metrics ingestion.

Daily metrics archives expose completed five-minute snapshots. A row is usable
only at its recorded ``create_time``; missing observations are never forward
filled.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import csv
import io
import json
from pathlib import Path
import re
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import pandas as pd

BASE_URL = "https://data.binance.vision/data/futures/um/daily/metrics"


@dataclass(frozen=True, slots=True)
class OpenInterestPoint:
    ts_ns: int
    open_interest: float
    open_interest_value: float | None
    taker_long_short_ratio: float | None


@dataclass(frozen=True, slots=True)
class LoadedOpenInterestMetrics:
    points: dict[int, OpenInterestPoint]
    source_files: tuple[Path, ...]
    quality: dict[str, object]


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _request_bytes(url: str, *, attempts: int = 4) -> bytes:
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-06/1.0"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=90) as response:  # noqa: S310 - fixed HTTPS host
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last_error}")


def _expected_checksum(text: str, filename: str) -> str:
    for line in text.splitlines():
        fields = line.strip().replace("*", " ").split()
        if fields and len(fields[0]) == 64 and (len(fields) == 1 or fields[-1].endswith(filename)):
            return fields[0].lower()
    raise ValueError(f"could not parse checksum for {filename}")


def download_daily_archive(symbol: str, day: date, destination: Path) -> tuple[Path, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    filename = f"{symbol}-metrics-{day.isoformat()}.zip"
    archive = destination / filename
    checksum_path = destination / f"{filename}.CHECKSUM"
    url = f"{BASE_URL}/{symbol}/{filename}"
    checksum_bytes = _request_bytes(f"{url}.CHECKSUM")
    expected = _expected_checksum(checksum_bytes.decode("utf-8"), filename)
    if not archive.exists() or _sha256_file(archive) != expected:
        temporary = archive.with_suffix(".zip.tmp")
        temporary.write_bytes(_request_bytes(url))
        actual = _sha256_file(temporary)
        if actual != expected:
            temporary.unlink(missing_ok=True)
            raise ValueError(f"checksum mismatch for {filename}: expected={expected}, actual={actual}")
        temporary.replace(archive)
    checksum_path.write_bytes(checksum_bytes)
    return archive, checksum_path


def _normalise_header(value: str) -> str:
    value = value.strip().replace("-", "_").replace(" ", "_")
    value = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return re.sub(r"_+", "_", value)


def _parse_timestamp(raw: str) -> pd.Timestamp:
    text = raw.strip()
    if re.fullmatch(r"[-+]?\d+(?:\.0+)?", text):
        value = int(float(text))
        unit = "us" if abs(value) >= 100_000_000_000_000 else "ms"
        return pd.to_datetime(value, unit=unit, utc=True)
    parsed = pd.Timestamp(text)
    return parsed.tz_localize("UTC") if parsed.tzinfo is None else parsed.tz_convert("UTC")


def _first(row: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if value not in {None, ""}:
            return value
    return None


def _read_archive(path: Path) -> list[OpenInterestPoint]:
    with zipfile.ZipFile(path) as bundle:
        members = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {path}, found {members}")
        with bundle.open(members[0]) as raw:
            rows = list(csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline="")))
    if len(rows) < 2:
        raise ValueError(f"metrics archive contains no data rows: {path}")
    header = [_normalise_header(value) for value in rows[0]]
    if "create_time" not in header and "timestamp" not in header:
        raise ValueError(f"unrecognised metrics header in {path}: {header}")
    points: list[OpenInterestPoint] = []
    for values in rows[1:]:
        if not values:
            continue
        if len(values) < len(header):
            raise ValueError(f"short metrics row in {path}: {values}")
        row = {header[index]: values[index].strip() for index in range(len(header))}
        ts_raw = _first(row, "create_time", "timestamp", "time")
        oi_raw = _first(row, "sum_open_interest", "open_interest", "sumopeninterest")
        if ts_raw is None or oi_raw is None:
            raise ValueError(f"required metrics fields absent in {path}: {row}")
        value_raw = _first(row, "sum_open_interest_value", "open_interest_value", "sumopeninterestvalue")
        ratio_raw = _first(row, "sum_taker_long_short_vol_ratio", "taker_long_short_ratio")
        oi = float(oi_raw)
        if oi <= 0.0:
            raise ValueError(f"non-positive open interest in {path}: {oi}")
        points.append(
            OpenInterestPoint(
                ts_ns=int(_parse_timestamp(ts_raw).value),
                open_interest=oi,
                open_interest_value=None if value_raw is None else float(value_raw),
                taker_long_short_ratio=None if ratio_raw is None else float(ratio_raw),
            ),
        )
    return points


def load_dates(symbol: str, days: Iterable[date], cache_root: str | Path) -> LoadedOpenInterestMetrics:
    dates = tuple(sorted(set(days)))
    if not dates:
        raise ValueError("at least one date is required")
    root = Path(cache_root).resolve() / symbol / "metrics"
    downloaded: dict[date, tuple[Path, Path]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(dates)))) as pool:
        futures = {pool.submit(download_daily_archive, symbol, day, root): day for day in dates}
        for future in as_completed(futures):
            downloaded[futures[future]] = future.result()
    all_points: list[OpenInterestPoint] = []
    for day in dates:
        all_points.extend(_read_archive(downloaded[day][0]))
    all_points.sort(key=lambda point: point.ts_ns)
    if not all_points:
        raise ValueError("no open-interest points loaded")
    mapping: dict[int, OpenInterestPoint] = {}
    for point in all_points:
        if point.ts_ns in mapping:
            raise ValueError(f"duplicate open-interest timestamp: {point.ts_ns}")
        mapping[point.ts_ns] = point
    gaps = [
        (all_points[index].ts_ns - all_points[index - 1].ts_ns) / 60_000_000_000
        for index in range(1, len(all_points))
    ]
    maximum_gap = max(gaps, default=0.0)
    if maximum_gap > 10.0:
        raise ValueError(f"open-interest metrics gap exceeds 10 minutes: {maximum_gap}")
    source_files: list[Path] = []
    for day in dates:
        source_files.extend(downloaded[day])
    quality: dict[str, object] = {
        "symbol": symbol,
        "provider": "Binance public data / USD-M futures daily metrics",
        "rows": len(mapping),
        "expected_cadence_minutes": 5,
        "maximum_gap_minutes": maximum_gap,
        "first_observed_utc": pd.Timestamp(all_points[0].ts_ns, tz="UTC").isoformat(),
        "last_observed_utc": pd.Timestamp(all_points[-1].ts_ns, tz="UTC").isoformat(),
        "timestamp_contract": "create_time is the first usable observation time; no forward fill",
        "archives": [path.name for path in source_files if path.suffix == ".zip"],
    }
    return LoadedOpenInterestMetrics(points=mapping, source_files=tuple(source_files), quality=quality)


def load_week(symbol: str, week_start: date, cache_root: str | Path) -> LoadedOpenInterestMetrics:
    return load_dates(symbol, [week_start + timedelta(days=offset) for offset in range(7)], cache_root)


def write_quality(path: str | Path, quality: dict[str, object]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(quality, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination
