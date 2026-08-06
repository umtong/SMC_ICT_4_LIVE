"""Checksum-verified Binance public futures kline ingestion.

The transformation only changes the timestamp convention: Binance open-time bars
are stamped at the exact end of the one-minute interval, which is when all OHLCV
fields are observable.  Raw archives and checksums remain in the data manifest.
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

import pandas as pd


BASE_URL = "https://data.binance.vision/data/futures/um/daily/klines"
COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)


@dataclass(frozen=True, slots=True)
class LoadedMarketData:
    frame: pd.DataFrame
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
        if not fields:
            continue
        if len(fields[0]) == 64 and (len(fields) == 1 or fields[-1].endswith(filename)):
            return fields[0].lower()
    raise ValueError(f"could not parse SHA-256 checksum for {filename!r}")


def download_daily_archive(symbol: str, day: date, destination: Path) -> tuple[Path, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    filename = f"{symbol}-1m-{day.isoformat()}.zip"
    archive = destination / filename
    checksum_path = destination / f"{filename}.CHECKSUM"
    url = f"{BASE_URL}/{symbol}/1m/{filename}"

    checksum_bytes = _request_bytes(f"{url}.CHECKSUM")
    checksum_text = checksum_bytes.decode("utf-8")
    expected = _expected_checksum(checksum_text, filename)
    if not archive.exists() or _sha256_file(archive) != expected:
        payload = _request_bytes(url)
        temporary = archive.with_suffix(".zip.tmp")
        temporary.write_bytes(payload)
        actual = _sha256_file(temporary)
        if actual != expected:
            temporary.unlink(missing_ok=True)
            raise ValueError(f"checksum mismatch for {filename}: expected={expected}, actual={actual}")
        temporary.replace(archive)
    checksum_path.write_bytes(checksum_bytes)
    return archive, checksum_path


def _timestamp_unit(value: int) -> str:
    # Binance changed some archive timestamps from milliseconds to microseconds
    # for files after 2025-01-01.  The candidate uses 2024 data but keeps the
    # parser explicit and safe for later validation.
    return "us" if value >= 100_000_000_000_000 else "ms"


def _read_archive(path: Path) -> pd.DataFrame:
    rows: list[list[str]] = []
    with zipfile.ZipFile(path) as bundle:
        csv_members = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
        if len(csv_members) != 1:
            raise ValueError(f"expected one CSV in {path}, found {csv_members}")
        with bundle.open(csv_members[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.reader(text)
            for row in reader:
                if not row:
                    continue
                if row[0].strip().lower() in {"open_time", "open time"}:
                    continue
                if len(row) < len(COLUMNS):
                    raise ValueError(f"short row in {path}: {row!r}")
                rows.append(row[: len(COLUMNS)])
    if not rows:
        raise ValueError(f"archive contains no kline rows: {path}")
    frame = pd.DataFrame(rows, columns=COLUMNS)
    for column in ("open_time", "close_time", "trades"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("int64")
    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_volume",
        "taker_buy_quote_volume",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")

    first_open = int(frame.iloc[0]["open_time"])
    unit = _timestamp_unit(first_open)
    open_ts = pd.to_datetime(frame["open_time"], unit=unit, utc=True)
    # ts_init/ts_event represent the first instant at which the complete bar is
    # knowable. This avoids treating an open-time stamp as a completed bar.
    frame.index = open_ts + pd.Timedelta(minutes=1)
    frame.index.name = "observed_time"
    return frame


def load_dates(
    symbol: str,
    days: Iterable[date],
    cache_root: str | Path,
    *,
    workers: int = 4,
) -> LoadedMarketData:
    dates = tuple(sorted(set(days)))
    if not dates:
        raise ValueError("at least one date is required")
    root = Path(cache_root).resolve() / symbol / "1m"
    downloaded: dict[date, tuple[Path, Path]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(dates)))) as pool:
        futures = {pool.submit(download_daily_archive, symbol, day, root): day for day in dates}
        for future in as_completed(futures):
            day = futures[future]
            downloaded[day] = future.result()

    frames = [_read_archive(downloaded[day][0]) for day in dates]
    frame = pd.concat(frames).sort_index()
    duplicated = int(frame.index.duplicated(keep=False).sum())
    if duplicated:
        raise ValueError(f"duplicate observed timestamps: {duplicated}")
    expected_index = pd.date_range(frame.index[0], frame.index[-1], freq="1min", tz="UTC")
    missing = expected_index.difference(frame.index)
    invalid_ohlc = int(
        (
            (frame["high"] < frame[["open", "close"]].max(axis=1))
            | (frame["low"] > frame[["open", "close"]].min(axis=1))
            | (frame["high"] < frame["low"])
            | (frame["volume"] < 0.0)
            | (frame["taker_buy_volume"] < 0.0)
            | (frame["taker_buy_volume"] > frame["volume"] + 1e-9)
        ).sum(),
    )
    if invalid_ohlc:
        raise ValueError(f"invalid OHLCV rows: {invalid_ohlc}")
    if len(missing):
        raise ValueError(f"missing one-minute bars: {len(missing)}; first={missing[:5].tolist()}")

    source_files: list[Path] = []
    for day in dates:
        source_files.extend(downloaded[day])
    quality: dict[str, object] = {
        "symbol": symbol,
        "interval": "1m",
        "provider": "Binance public data / USDT-M futures",
        "start_observed_utc": frame.index[0].isoformat(),
        "end_observed_utc": frame.index[-1].isoformat(),
        "rows": int(len(frame)),
        "expected_rows": int(len(expected_index)),
        "missing_rows": int(len(missing)),
        "duplicate_rows": duplicated,
        "invalid_ohlcv_rows": invalid_ohlc,
        "timestamp_contract": "source open_time + 1 minute = completed-bar observed time",
        "archives": [path.name for path in source_files if path.suffix == ".zip"],
    }
    return LoadedMarketData(frame=frame, source_files=tuple(source_files), quality=quality)


def load_week(symbol: str, week_start: date, cache_root: str | Path) -> LoadedMarketData:
    days = [week_start + timedelta(days=offset) for offset in range(7)]
    loaded = load_dates(symbol, days, cache_root)
    expected = 7 * 24 * 60
    if len(loaded.frame) != expected:
        raise ValueError(f"week must contain {expected} one-minute bars, found {len(loaded.frame)}")
    return loaded


def write_quality(path: str | Path, quality: dict[str, object]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(quality, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination
