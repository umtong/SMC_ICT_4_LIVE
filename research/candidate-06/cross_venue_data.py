"""Checksum-verified Binance spot context ingestion for candidate-06 CVPD.

The existing market_data module remains the source of the USDT-M perpetual
execution bars.  This module loads BTCUSDT spot bars with the same completed-bar
observed-time convention and rejects any timestamp mismatch before Nautilus runs.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

from market_data import (
    LoadedMarketData,
    _expected_checksum,
    _read_archive,
    _request_bytes,
    _sha256_file,
)

SPOT_BASE_URL = "https://data.binance.vision/data/spot/daily/klines"


def download_spot_daily_archive(symbol: str, day: date, destination: Path) -> tuple[Path, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    filename = f"{symbol}-1m-{day.isoformat()}.zip"
    archive = destination / filename
    checksum_path = destination / f"{filename}.CHECKSUM"
    url = f"{SPOT_BASE_URL}/{symbol}/1m/{filename}"
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
                f"spot checksum mismatch for {filename}: expected={expected}, actual={actual}",
            )
        temporary.replace(archive)
    checksum_path.write_bytes(checksum_bytes)
    return archive, checksum_path


def load_spot_dates(
    symbol: str,
    days: Iterable[date],
    cache_root: str | Path,
    *,
    workers: int = 4,
) -> LoadedMarketData:
    dates = tuple(sorted(set(days)))
    if not dates:
        raise ValueError("at least one spot date is required")
    root = Path(cache_root).resolve() / "spot" / symbol / "1m"
    downloaded: dict[date, tuple[Path, Path]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(dates)))) as pool:
        futures = {
            pool.submit(download_spot_daily_archive, symbol, day, root): day
            for day in dates
        }
        for future in as_completed(futures):
            downloaded[futures[future]] = future.result()
    frames = [_read_archive(downloaded[day][0]) for day in dates]
    frame = pd.concat(frames).sort_index()
    duplicate_rows = int(frame.index.duplicated(keep=False).sum())
    if duplicate_rows:
        raise ValueError(f"duplicate spot observed timestamps: {duplicate_rows}")
    expected_index = pd.date_range(frame.index[0], frame.index[-1], freq="1min", tz="UTC")
    missing = expected_index.difference(frame.index)
    if len(missing):
        raise ValueError(f"missing spot one-minute bars: {len(missing)}; first={missing[:5].tolist()}")
    invalid = int(
        (
            (frame["high"] < frame[["open", "close"]].max(axis=1))
            | (frame["low"] > frame[["open", "close"]].min(axis=1))
            | (frame["high"] < frame["low"])
            | (frame["volume"] < 0.0)
            | (frame["taker_buy_volume"] < 0.0)
            | (frame["taker_buy_volume"] > frame["volume"] + 1e-9)
        ).sum(),
    )
    if invalid:
        raise ValueError(f"invalid spot OHLCV rows: {invalid}")
    source_files: list[Path] = []
    for day in dates:
        source_files.extend(downloaded[day])
    quality: dict[str, object] = {
        "symbol": symbol,
        "market": "Binance spot",
        "interval": "1m",
        "provider": "Binance public data",
        "start_observed_utc": frame.index[0].isoformat(),
        "end_observed_utc": frame.index[-1].isoformat(),
        "rows": int(len(frame)),
        "expected_rows": int(len(expected_index)),
        "missing_rows": int(len(missing)),
        "duplicate_rows": duplicate_rows,
        "invalid_ohlcv_rows": invalid,
        "timestamp_contract": "source open_time + 1 minute = completed-bar observed time",
        "archives": [path.name for path in source_files if path.suffix == ".zip"],
    }
    return LoadedMarketData(frame=frame, source_files=tuple(source_files), quality=quality)


def load_spot_week(symbol: str, week_start: date, cache_root: str | Path) -> LoadedMarketData:
    loaded = load_spot_dates(
        symbol,
        [week_start + timedelta(days=offset) for offset in range(7)],
        cache_root,
    )
    expected = 7 * 24 * 60
    if len(loaded.frame) != expected:
        raise ValueError(f"spot week must contain {expected} one-minute bars, found {len(loaded.frame)}")
    return loaded


def assert_synchronized_completed_bars(perpetual: pd.DataFrame, spot: pd.DataFrame) -> None:
    if len(perpetual) != len(spot):
        raise ValueError(
            f"cross-venue row mismatch: perpetual={len(perpetual)}, spot={len(spot)}",
        )
    if not perpetual.index.equals(spot.index):
        missing_spot = perpetual.index.difference(spot.index)
        missing_perp = spot.index.difference(perpetual.index)
        raise ValueError(
            "cross-venue completed timestamps differ: "
            f"missing_spot={missing_spot[:5].tolist()}, missing_perpetual={missing_perp[:5].tolist()}",
        )
