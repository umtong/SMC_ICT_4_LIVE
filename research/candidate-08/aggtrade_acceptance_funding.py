"""Checksum-verified official Binance USD-M perpetual funding-rate loader.

This module is deliberately independent of NautilusTrader so archive, schema, timestamp, and
boundary contracts can be unit-tested without importing the execution runtime. The runner converts
its normalized rows into NautilusTrader ``FundingRateUpdate`` objects.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
from math import isfinite
from pathlib import Path
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import pandas as pd


REQUIRED_COLUMNS = {
    "calc_time",
    "funding_interval_hours",
    "last_funding_rate",
}

# Official Binance funding archives occasionally stamp an intended settlement boundary
# a few milliseconds late. This is data timestamp jitter, not a missing settlement.
# Canonicalization is allowed only inside one second; material off-boundary rows fail.
MAX_FUNDING_BOUNDARY_JITTER_NS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class FundingSource:
    period: str
    url: str
    checksum_url: str
    sha256: str
    size_bytes: int
    rows: int


@dataclass(frozen=True, slots=True)
class LoadedFundingRates:
    frame: pd.DataFrame
    source_files: tuple[FundingSource, ...]
    quality: dict[str, object]


class FundingDataError(RuntimeError):
    """Raised when the official funding archive violates the research data contract."""


def _month_starts(start: datetime, end: datetime) -> Iterable[datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if end <= start:
        raise ValueError("end must be after start")
    cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    inclusive_end = end - timedelta(microseconds=1)
    final = datetime(inclusive_end.year, inclusive_end.month, 1, tzinfo=timezone.utc)
    while cursor <= final:
        yield cursor
        if cursor.month == 12:
            cursor = datetime(cursor.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            cursor = datetime(cursor.year, cursor.month + 1, 1, tzinfo=timezone.utc)


def _download(url: str, *, retries: int = 4, timeout: int = 120) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-08/1.0"})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise FundingDataError(f"download failed after {retries} attempts: {url}: {last_error}")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_monthly_archive(
    cache_dir: Path,
    symbol: str,
    month: datetime,
) -> tuple[Path, FundingSource]:
    period = month.strftime("%Y-%m")
    filename = f"{symbol}-fundingRate-{period}.zip"
    base = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}"
    url = f"{base}/{filename}"
    checksum_url = f"{url}.CHECKSUM"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / filename

    checksum_text = _download(checksum_url).decode("utf-8").strip()
    expected = checksum_text.split()[0].lower()
    if len(expected) != 64:
        raise FundingDataError(f"invalid checksum payload for {filename}: {checksum_text!r}")

    if destination.exists() and _sha256_file(destination) != expected:
        destination.unlink()
    if not destination.exists():
        payload = _download(url, timeout=300)
        actual = sha256(payload).hexdigest()
        if actual != expected:
            raise FundingDataError(f"SHA-256 mismatch for {filename}: {actual} != {expected}")
        temporary = destination.with_suffix(".zip.tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)

    actual = _sha256_file(destination)
    return destination, FundingSource(
        period=period,
        url=url,
        checksum_url=checksum_url,
        sha256=actual,
        size_bytes=destination.stat().st_size,
        rows=0,
    )


def _timestamp_unit(values: pd.Series) -> str:
    materialized = pd.to_numeric(values, errors="coerce").dropna()
    if materialized.empty:
        raise FundingDataError("funding archive contains no numeric calc_time values")
    median = float(materialized.iloc[len(materialized) // 2])
    return "us" if median > 100_000_000_000_000 else "ms"


def _read_month(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise FundingDataError(f"expected one CSV in {path.name}, found {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(io.BytesIO(payload), low_memory=False)
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise FundingDataError(
            f"funding archive schema changed for {path.name}: missing {sorted(missing)}"
        )
    return frame


def _normalize_funding_frame(
    frame: pd.DataFrame,
    *,
    start: datetime,
    end: datetime,
) -> tuple[pd.DataFrame, dict[str, object]]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if end <= start:
        raise ValueError("end must be after start")

    data = frame.loc[:, sorted(REQUIRED_COLUMNS)].copy()
    for column in REQUIRED_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    source_rows = len(data.index)
    data = data.dropna(subset=sorted(REQUIRED_COLUMNS)).copy()
    invalid_numeric_rows = source_rows - len(data.index)
    if data.empty:
        raise FundingDataError("funding archive produced no valid numeric rows")

    unit = _timestamp_unit(data["calc_time"])
    data.index = pd.to_datetime(data.pop("calc_time").astype("int64"), unit=unit, utc=True)
    data.index.name = "funding_time"
    data["funding_rate"] = data.pop("last_funding_rate").astype(float)
    hours = data.pop("funding_interval_hours").astype(float)
    rounded_hours = hours.round()
    if not ((hours - rounded_hours).abs() <= 1e-9).all():
        raise FundingDataError("funding_interval_hours contains non-integral values")
    data["funding_interval_minutes"] = rounded_hours.astype("int64") * 60

    invalid_rate_count = int((~data["funding_rate"].map(isfinite)).sum())
    invalid_interval_count = int(
        ((data["funding_interval_minutes"] <= 0) | (data["funding_interval_minutes"] > 480)).sum()
    )
    if invalid_rate_count or invalid_interval_count:
        raise FundingDataError(
            "invalid funding rows: "
            f"rate={invalid_rate_count}, interval={invalid_interval_count}"
        )

    raw_timestamps = data.index
    canonical_timestamps: list[int] = []
    boundary_jitter_ns: list[int] = []
    boundary_failures = 0
    for timestamp, interval_minutes in zip(
        raw_timestamps,
        data["funding_interval_minutes"],
        strict=True,
    ):
        raw_ns = int(timestamp.as_unit("ns").value)
        interval_ns = int(interval_minutes) * 60_000_000_000
        remainder = raw_ns % interval_ns
        if remainder <= interval_ns // 2:
            canonical_ns = raw_ns - remainder
            jitter_ns = remainder
        else:
            canonical_ns = raw_ns + (interval_ns - remainder)
            jitter_ns = interval_ns - remainder
        if jitter_ns > MAX_FUNDING_BOUNDARY_JITTER_NS:
            boundary_failures += 1
        canonical_timestamps.append(canonical_ns)
        boundary_jitter_ns.append(jitter_ns)
    if boundary_failures:
        raise FundingDataError(
            "funding calc_time did not land within the verified settlement-boundary "
            f"jitter tolerance: {boundary_failures} rows"
        )
    data.index = pd.to_datetime(canonical_timestamps, unit="ns", utc=True)
    data.index.name = "funding_time"
    data = data.sort_index()
    duplicate_rows = int(data.index.duplicated(keep=False).sum())
    if duplicate_rows:
        conflicting = False
        for _, group in data.loc[data.index.duplicated(keep=False)].groupby(level=0):
            if (
                group["funding_rate"].nunique(dropna=False) > 1
                or group["funding_interval_minutes"].nunique(dropna=False) > 1
            ):
                conflicting = True
                break
        if conflicting:
            raise FundingDataError("conflicting duplicate funding timestamp rows")
        data = data.loc[~data.index.duplicated(keep="last")].copy()

    for timestamp, interval_minutes in zip(
        data.index,
        data["funding_interval_minutes"],
        strict=True,
    ):
        timestamp_ns = int(timestamp.as_unit("ns").value)
        interval_ns = int(interval_minutes) * 60_000_000_000
        if timestamp_ns % interval_ns != 0:
            raise FundingDataError("canonical funding timestamp was not on its boundary")

    data = data.loc[(data.index >= start) & (data.index < end)].copy()
    if data.empty:
        raise FundingDataError(f"no funding rows in requested interval {start} to {end}")

    deltas = data.index.to_series().diff().dt.total_seconds().div(60.0)
    previous_intervals = data["funding_interval_minutes"].shift(1).astype(float)
    current_intervals = data["funding_interval_minutes"].astype(float)
    allowed = pd.concat([previous_intervals, current_intervals], axis=1).max(axis=1)
    internal_gap_mask = deltas.notna() & (deltas > allowed + 1e-9)
    gap_count = int(internal_gap_mask.sum())
    if gap_count:
        raise FundingDataError(f"funding archive contains {gap_count} internal settlement gaps")

    quality: dict[str, object] = {
        "rows": len(data.index),
        "source_rows": source_rows,
        "invalid_numeric_rows_removed": invalid_numeric_rows,
        "duplicate_rows_observed": duplicate_rows,
        "internal_gap_count": gap_count,
        "max_gap_minutes": float(deltas.max()) if deltas.notna().any() else 0.0,
        "max_boundary_jitter_milliseconds": max(boundary_jitter_ns, default=0) / 1_000_000.0,
        "timestamp_unit_detected": unit,
        "first_funding_time": data.index[0].isoformat(),
        "last_funding_time": data.index[-1].isoformat(),
        "interval_minutes": sorted(int(value) for value in data["funding_interval_minutes"].unique()),
    }
    return data[["funding_rate", "funding_interval_minutes"]], quality


def load_official_funding_rates(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
) -> LoadedFundingRates:
    frames: list[pd.DataFrame] = []
    sources: list[FundingSource] = []
    for month in _month_starts(start, end):
        path, source = _verified_monthly_archive(cache_dir / symbol, symbol, month)
        month_frame = _read_month(path)
        frames.append(month_frame)
        sources.append(
            FundingSource(
                period=source.period,
                url=source.url,
                checksum_url=source.checksum_url,
                sha256=source.sha256,
                size_bytes=source.size_bytes,
                rows=len(month_frame.index),
            )
        )
    if not frames:
        raise FundingDataError("no funding archive months selected")
    normalized, quality = _normalize_funding_frame(
        pd.concat(frames, ignore_index=True),
        start=start,
        end=end,
    )
    return LoadedFundingRates(
        frame=normalized,
        source_files=tuple(sources),
        quality=quality,
    )


FundingObservation = tuple[int, float, int]


def funding_observations_from_frame(frame: pd.DataFrame) -> tuple[FundingObservation, ...]:
    """Return immutable, timestamp-ordered funding observations for causal cost estimates.

    Each observation is ``(settlement_time_ns, funding_rate, interval_minutes)``. The archive rate
    is considered observable only at its own ``calc_time``; downstream lookups must never use a row
    whose timestamp is later than the signal being sized.
    """

    required = {"funding_rate", "funding_interval_minutes"}
    missing = required - set(frame.columns)
    if missing:
        raise FundingDataError(f"normalized funding frame missing {sorted(missing)}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise FundingDataError("normalized funding frame must use a DatetimeIndex")
    materialized: list[FundingObservation] = []
    last_timestamp = -1
    for timestamp, row in frame.sort_index().iterrows():
        timestamp_ns = int(timestamp.as_unit("ns").value)
        rate = float(row["funding_rate"])
        interval_minutes = int(row["funding_interval_minutes"])
        if timestamp_ns <= last_timestamp:
            raise FundingDataError("funding observations must have strictly increasing timestamps")
        if not isfinite(rate) or not 0 < interval_minutes <= 480:
            raise FundingDataError("invalid normalized funding observation")
        materialized.append((timestamp_ns, rate, interval_minutes))
        last_timestamp = timestamp_ns
    return tuple(materialized)


def causal_funding_cost_state(
    observations: tuple[FundingObservation, ...],
    *,
    signal_time_ns: int,
    entry_price: float,
    maximum_hold_minutes: int,
) -> dict[str, float | int] | None:
    """Estimate funding cost using only the last settlement observable at the signal time.

    The estimate reserves the absolute value of the last observed funding rate for every scheduled
    settlement boundary that can occur during the maximum holding period. Credits are never counted
    as expected reward. This is not a fitted risk multiplier: it is a causal, directly attributable
    expected execution cost which is later replaced by actual NautilusTrader funding settlements in
    account NAV.
    """

    if signal_time_ns < 0:
        raise ValueError("signal_time_ns must be non-negative")
    if not isfinite(entry_price) or entry_price <= 0:
        raise ValueError("entry_price must be finite and positive")
    if maximum_hold_minutes <= 0:
        raise ValueError("maximum_hold_minutes must be positive")
    if not observations:
        return None

    timestamps = [item[0] for item in observations]
    position = bisect_right(timestamps, int(signal_time_ns)) - 1
    if position < 0:
        return None
    observed_time_ns, rate, interval_minutes = observations[position]
    interval_ns = int(interval_minutes) * 60 * 1_000_000_000
    next_boundary_ns = (int(signal_time_ns) // interval_ns + 1) * interval_ns
    hold_end_ns = int(signal_time_ns) + int(maximum_hold_minutes) * 60 * 1_000_000_000
    if next_boundary_ns > hold_end_ns:
        crossings = 0
    else:
        crossings = 1 + (hold_end_ns - next_boundary_ns) // interval_ns
    minutes_to_next = (next_boundary_ns - int(signal_time_ns)) / 60_000_000_000.0
    absolute_rate = abs(float(rate))
    reserve = float(crossings) * absolute_rate * float(entry_price)
    return {
        "funding_observed_time_ns": int(observed_time_ns),
        "funding_rate_observed": float(rate),
        "expected_funding_rate_abs": absolute_rate,
        "funding_interval_minutes": int(interval_minutes),
        "minutes_to_next_funding": float(minutes_to_next),
        "expected_funding_crossings": int(crossings),
        "expected_funding_reserve_per_unit": float(reserve),
    }
