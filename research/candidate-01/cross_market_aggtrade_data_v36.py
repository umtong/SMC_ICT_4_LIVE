"""Causal Binance Vision spot aggTrade loader for candidate 01 v36.

The existing :mod:`aggtrade_data` module remains the authoritative USD-M
futures loader.  This module adds only the corresponding SPOT archive routing
while reusing the same checksum, timestamp normalization and immutable
``AggTrade`` representation.  It contains no signal, fill, PnL or NAV logic.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from zipfile import ZipFile

from aggtrade_data import (
    AggTradeDownload,
    _download_to_path,
    _expected_checksum,
    _sha256_file,
    iter_download,
    iter_downloads,
    utc_days,
)

SPOT_BASE = "https://data.binance.vision/data/spot/daily/aggTrades"


def spot_archive_url(symbol: str, day: date) -> str:
    value = day.isoformat()
    return f"{SPOT_BASE}/{symbol}/{symbol}-aggTrades-{value}.zip"


def _download_spot_one(
    *,
    symbol: str,
    day: date,
    cache_dir: Path,
) -> AggTradeDownload:
    url = spot_archive_url(symbol, day)
    archive_name = url.rsplit("/", 1)[-1]
    destination = cache_dir / "spot" / symbol / archive_name
    checksum_url = url + ".CHECKSUM"
    checksum_path = destination.with_suffix(destination.suffix + ".CHECKSUM")
    _download_to_path(url, destination)
    _download_to_path(checksum_url, checksum_path)
    expected = _expected_checksum(checksum_path, archive_name)
    actual = _sha256_file(destination)
    if actual != expected:
        destination.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        raise ValueError(
            f"checksum mismatch for {archive_name}: expected {expected}, got {actual}",
        )
    with ZipFile(destination) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {destination}, found {members}")
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"corrupt ZIP member {bad} in {destination}")
    return AggTradeDownload(
        symbol=symbol,
        day=day.isoformat(),
        url=url,
        checksum_url=checksum_url,
        path=str(destination),
        checksum_path=str(checksum_path),
        size_bytes=destination.stat().st_size,
        sha256=actual,
        expected_sha256=expected,
    )


def download_spot_aggtrade_days(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    workers: int = 4,
) -> list[AggTradeDownload]:
    days = utc_days(start, end)
    if not days:
        return []
    records: list[AggTradeDownload] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(days)))) as executor:
        futures = {
            executor.submit(
                _download_spot_one,
                symbol=symbol,
                day=day,
                cache_dir=cache_dir,
            ): day
            for day in days
        }
        for future in as_completed(futures):
            records.append(future.result())
    return sorted(records, key=lambda item: item.day)


__all__ = [
    "SPOT_BASE",
    "download_spot_aggtrade_days",
    "iter_download",
    "iter_downloads",
    "spot_archive_url",
]
