"""Checksum-verified Binance USD-M five-minute positioning metrics.

The archive supplies completed open-interest and taker-flow snapshots as causal
side-channel observations. Trading, fills, positions and NAV remain entirely in
NautilusTrader.
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
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


BASE_URL = "https://data.binance.vision/data/futures/um/daily/metrics"
COLUMNS = (
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)


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
    raise ValueError(f"could not parse SHA-256 checksum for {filename!r}")


@dataclass(frozen=True, slots=True)
class FuturesMetric:
    ts_ns: int
    open_interest: float
    open_interest_value: float
    top_account_long_short: float
    top_position_long_short: float
    all_account_long_short: float
    taker_buy_sell_ratio: float

    @property
    def signed_taker_ratio(self) -> float:
        ratio = self.taker_buy_sell_ratio
        if ratio <= 0.0:
            return 0.0
        return (ratio - 1.0) / (ratio + 1.0)


@dataclass(frozen=True, slots=True)
class LoadedFuturesMetrics:
    observations: dict[int, FuturesMetric]
    source_files: tuple[Path, ...]
    quality: dict[str, object]


def download_daily_archive(symbol: str, day: date, destination: Path) -> tuple[Path, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    filename = f"{symbol}-metrics-{day.isoformat()}.zip"
    archive = destination / filename
    checksum_path = destination / f"{filename}.CHECKSUM"
    url = f"{BASE_URL}/{symbol}/{filename}"
    checksum_bytes = _request_bytes(f"{url}.CHECKSUM")
    expected = _expected_checksum(checksum_bytes.decode("utf-8"), filename)
    if not archive.exists() or _sha256_file(archive) != expected:
        payload = _request_bytes(url)
        temporary = archive.with_suffix(".zip.tmp")
        temporary.write_bytes(payload)
        actual = _sha256_file(temporary)
        if actual != expected:
            temporary.unlink(missing_ok=True)
            raise ValueError(
                f"checksum mismatch for {filename}: expected={expected}, actual={actual}",
            )
        temporary.replace(archive)
    checksum_path.write_bytes(checksum_bytes)
    return archive, checksum_path


def _timestamp_ns(value: str) -> int:
    text = value.strip()
    try:
        raw = int(float(text))
    except ValueError:
        import pandas as pd

        stamp = pd.Timestamp(text)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        else:
            stamp = stamp.tz_convert("UTC")
        return int(stamp.value)
    if raw >= 100_000_000_000_000_000:
        return raw
    if raw >= 100_000_000_000_000:
        return raw * 1_000
    if raw >= 100_000_000_000:
        return raw * 1_000_000
    if raw >= 1_000_000_000:
        return raw * 1_000_000_000
    raise ValueError(f"unsupported metrics timestamp: {value!r}")


def _read_archive(path: Path) -> list[FuturesMetric]:
    with zipfile.ZipFile(path) as bundle:
        members = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {path}, found {members}")
        with bundle.open(members[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            rows = [row for row in reader if row]
    if not rows:
        raise ValueError(f"archive contains no rows: {path}")
    first = [value.strip().lower() for value in rows[0]]
    has_header = "create_time" in first or "sum_open_interest" in first
    data_rows = rows[1:] if has_header else rows
    observations: list[FuturesMetric] = []
    for row in data_rows:
        if len(row) < len(COLUMNS):
            raise ValueError(f"short metrics row in {path}: {row!r}")
        values = row[: len(COLUMNS)]
        observation = FuturesMetric(
            ts_ns=_timestamp_ns(values[0]),
            open_interest=float(values[2]),
            open_interest_value=float(values[3]),
            top_account_long_short=float(values[4]),
            top_position_long_short=float(values[5]),
            all_account_long_short=float(values[6]),
            taker_buy_sell_ratio=float(values[7]),
        )
        if observation.open_interest <= 0.0 or observation.open_interest_value <= 0.0:
            raise ValueError(f"nonpositive open interest in {path}: {row!r}")
        observations.append(observation)
    return observations


def load_dates(
    symbol: str,
    days: Iterable[date],
    cache_root: str | Path,
    *,
    workers: int = 4,
) -> LoadedFuturesMetrics:
    dates = tuple(sorted(set(days)))
    if not dates:
        raise ValueError("at least one date is required")
    root = Path(cache_root).resolve() / symbol / "metrics"
    downloaded: dict[date, tuple[Path, Path]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(dates)))) as pool:
        futures = {pool.submit(download_daily_archive, symbol, day, root): day for day in dates}
        for future in as_completed(futures):
            downloaded[futures[future]] = future.result()

    observations: dict[int, FuturesMetric] = {}
    for day in dates:
        for item in _read_archive(downloaded[day][0]):
            if item.ts_ns in observations:
                raise ValueError(f"duplicate metrics timestamp: {item.ts_ns}")
            observations[item.ts_ns] = item
    observations = dict(sorted(observations.items()))
    if not observations:
        raise ValueError(f"no metrics observations for {symbol} over {dates}")
    timestamps = list(observations)
    gaps = [
        (left, right)
        for left, right in zip(timestamps, timestamps[1:])
        if right - left != 5 * 60 * 1_000_000_000
    ]
    if gaps:
        raise ValueError(f"non-five-minute metrics gaps: count={len(gaps)}, first={gaps[:5]}")

    source_files: list[Path] = []
    for day in dates:
        source_files.extend(downloaded[day])
    quality: dict[str, object] = {
        "symbol": symbol,
        "provider": "Binance public data / USD-M futures metrics",
        "archives": [path.name for path in source_files if path.suffix == ".zip"],
        "observations": len(observations),
        "first_observed_utc_ns": timestamps[0],
        "last_observed_utc_ns": timestamps[-1],
        "cadence_minutes": 5,
        "missing_intervals": len(gaps),
        "timestamp_contract": "published five-minute metric timestamp is used only when its completed snapshot is observable",
        "fields": list(COLUMNS),
    }
    return LoadedFuturesMetrics(
        observations=observations,
        source_files=tuple(source_files),
        quality=quality,
    )


def load_week(symbol: str, week_start: date, cache_root: str | Path) -> LoadedFuturesMetrics:
    return load_dates(
        symbol,
        [week_start + timedelta(days=offset) for offset in range(7)],
        cache_root,
    )


def write_quality(path: str | Path, quality: dict[str, object]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(quality, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination
