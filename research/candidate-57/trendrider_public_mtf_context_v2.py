"""Strict-as-of public TrendRider informative-timeframe context.

The external source uses each pair's completed 4h trend/ADX and completed daily
EMA200.  This module reuses the project's checksum-verified Binance Vision
archive approach, computes only the visible public indicators, writes a compact
sidecar, and serves the latest row whose close timestamp is not later than the
decision.  It never calls an exchange trading or matching API.
"""
from __future__ import annotations

from bisect import bisect_right
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import time as time_module
from typing import Any, Iterable
from urllib.request import urlretrieve
import zipfile

from router_picasso import BarObservation, _adx, _ema

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BASE_URL = "https://data.binance.vision/data/futures/um/monthly/klines"


@dataclass(frozen=True, slots=True)
class MTFObservation:
    observed_time_ns: int
    ready: bool
    daily_ema_200: float = math.nan
    pair_4h_is_bull: int = 0
    pair_4h_adx: float = math.nan


class MTFContextStore:
    def __init__(self, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if int(payload.get("schema_version") or 0) != 2:
            raise RuntimeError("invalid TrendRider MTF sidecar schema")
        self._rows: dict[str, list[dict[str, Any]]] = {}
        self._times: dict[str, list[int]] = {}
        for symbol in SYMBOLS:
            rows = list((payload.get("symbols") or {}).get(symbol) or [])
            times = [int(row["observed_time_ns"]) for row in rows]
            if not rows or any(right <= left for left, right in zip(times, times[1:])):
                raise RuntimeError(f"invalid or non-monotonic MTF rows for {symbol}")
            self._rows[symbol] = rows
            self._times[symbol] = times
        self.metadata = payload.get("metadata") or {}

    def observation(self, symbol: str, ts_event: int) -> MTFObservation:
        times = self._times.get(symbol)
        rows = self._rows.get(symbol)
        if not times or not rows:
            return MTFObservation(0, False)
        index = bisect_right(times, int(ts_event)) - 1
        if index < 0:
            return MTFObservation(0, False)
        row = rows[index]
        observed = int(row["observed_time_ns"])
        if observed > int(ts_event):
            raise RuntimeError("future TrendRider MTF row reached the router")
        daily = _number(row.get("daily_ema_200"))
        adx = _number(row.get("pair_4h_adx"))
        ready = bool(row.get("ready")) and math.isfinite(daily) and math.isfinite(adx)
        return MTFObservation(
            observed_time_ns=observed,
            ready=ready,
            daily_ema_200=daily,
            pair_4h_is_bull=int(row.get("pair_4h_is_bull") or 0),
            pair_4h_adx=adx,
        )


_STORE: MTFContextStore | None = None


def configure_context(path: str | Path) -> None:
    global _STORE
    _STORE = MTFContextStore(path)


def context_observation(symbol: str, ts_event: int) -> MTFObservation:
    if _STORE is None:
        return MTFObservation(0, False)
    return _STORE.observation(symbol, ts_event)


def _number(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _timestamp_ns(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def _raw_timestamp_ns(value: Any) -> int:
    raw = int(str(value).strip())
    # Binance Vision migrated some archives from milliseconds to microseconds.
    return raw * (1_000 if abs(raw) > 10**14 else 1_000_000)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _months(start: date, end: date) -> Iterable[str]:
    cursor = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cursor <= last:
        yield f"{cursor.year:04d}-{cursor.month:02d}"
        cursor = date(
            cursor.year + (1 if cursor.month == 12 else 0),
            1 if cursor.month == 12 else cursor.month + 1,
            1,
        )


def _download_checked(symbol: str, interval: str, month: str, cache: Path) -> tuple[Path, dict[str, Any]]:
    filename = f"{symbol}-{interval}-{month}.zip"
    url = f"{BASE_URL}/{symbol}/{interval}/{filename}"
    directory = cache / symbol / interval
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    error: Exception | None = None
    for attempt in range(6):
        try:
            if not archive.exists():
                urlretrieve(url, archive)  # noqa: S310 - fixed public Binance archive
            if not checksum.exists():
                urlretrieve(url + ".CHECKSUM", checksum)  # noqa: S310
            expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
            actual = _sha256(archive)
            if actual != expected:
                archive.unlink(missing_ok=True)
                checksum.unlink(missing_ok=True)
                raise RuntimeError(f"checksum mismatch {actual} != {expected}")
            return archive, {
                "month": month,
                "archive": str(archive),
                "checksum": str(checksum),
                "sha256": actual,
                "size_bytes": archive.stat().st_size,
                "url": url,
            }
        except Exception as exc:  # pragma: no cover - network retry path
            error = exc
            archive.unlink(missing_ok=True)
            checksum.unlink(missing_ok=True)
            time_module.sleep(min(2**attempt, 12))
    raise RuntimeError(f"Binance Vision download failed for {symbol} {interval} {month}: {error}")


def _read_archive(path: Path) -> list[list[Any]]:
    rows: list[list[Any]] = []
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected Binance archive members in {path}: {names}")
        with archive.open(names[0]) as raw_stream:
            stream = io.TextIOWrapper(raw_stream, encoding="utf-8")
            reader = csv.reader(stream)
            for row in reader:
                if not row:
                    continue
                first = str(row[0]).strip()
                if not first.lstrip("-").isdigit():
                    continue
                if len(row) < 7:
                    raise RuntimeError(f"unexpected Binance kline row in {path}: {row}")
                rows.append(row)
    return rows


def fetch_klines(
    symbol: str,
    interval: str,
    start: date,
    end: date,
    cache: Path | None = None,
) -> tuple[list[list[Any]], list[dict[str, Any]]]:
    if end < start:
        raise ValueError("end precedes start")
    cache_root = cache or Path(".cache/trendrider-public-mtf-v2")
    start_ns = _timestamp_ns(start)
    end_ns = _timestamp_ns(end + timedelta(days=1)) - 1
    rows: list[list[Any]] = []
    evidence: list[dict[str, Any]] = []
    for month in _months(start, end):
        archive, item = _download_checked(symbol, interval, month, cache_root)
        evidence.append(item)
        for row in _read_archive(archive):
            open_ns = _raw_timestamp_ns(row[0])
            if start_ns <= open_ns <= end_ns:
                rows.append(row)
    rows.sort(key=lambda row: _raw_timestamp_ns(row[0]))
    if any(
        _raw_timestamp_ns(right[0]) <= _raw_timestamp_ns(left[0])
        for left, right in zip(rows, rows[1:])
    ):
        raise RuntimeError(f"non-monotonic Binance klines for {symbol} {interval}")
    if not rows:
        raise RuntimeError(f"empty Binance Vision range for {symbol} {interval} {start}..{end}")
    return rows, evidence


def _bars(rows: Iterable[list[Any]]) -> list[BarObservation]:
    return [
        BarObservation(
            ts_event=_raw_timestamp_ns(row[6]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in rows
    ]


def _source_rows(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Public daily EMA200 needs a long causal seed.  280 calendar days gives
    # enough completed daily bars without fitting a lookback to outcomes.
    daily_start = start - timedelta(days=280)
    four_hour_start = start - timedelta(days=55)
    daily_raw, daily_evidence = fetch_klines(symbol, "1d", daily_start, end, cache)
    four_raw, four_evidence = fetch_klines(symbol, "4h", four_hour_start, end, cache)
    daily = _bars(daily_raw)
    four = _bars(four_raw)
    daily_ema = _ema([float(bar.close) for bar in daily], 200)
    four_ema_50 = _ema([float(bar.close) for bar in four], 50)
    four_ema_200 = _ema([float(bar.close) for bar in four], 200)
    four_adx = _adx(four, 14)

    daily_points = [
        (int(bar.ts_event), float(daily_ema[index]))
        for index, bar in enumerate(daily)
        if math.isfinite(float(daily_ema[index]))
    ]
    four_points = [
        (
            int(bar.ts_event),
            int(
                float(bar.close) > float(four_ema_200[index])
                and float(four_ema_50[index]) > float(four_ema_200[index])
            ),
            float(four_adx[index]),
        )
        for index, bar in enumerate(four)
        if all(
            math.isfinite(float(value))
            for value in (four_ema_50[index], four_ema_200[index], four_adx[index])
        )
    ]
    if not daily_points or not four_points:
        raise RuntimeError(f"insufficient public informative history for {symbol}")

    start_ns = _timestamp_ns(start)
    end_ns = _timestamp_ns(end + timedelta(days=1)) - 1
    boundaries = sorted(
        timestamp
        for timestamp, *_ in four_points
        if start_ns <= timestamp <= end_ns
    )
    daily_times = [item[0] for item in daily_points]
    four_times = [item[0] for item in four_points]
    output: list[dict[str, Any]] = []
    for timestamp in boundaries:
        daily_index = bisect_right(daily_times, timestamp) - 1
        four_index = bisect_right(four_times, timestamp) - 1
        if daily_index < 0 or four_index < 0:
            continue
        daily_ts, daily_value = daily_points[daily_index]
        four_ts, four_bull, adx_value = four_points[four_index]
        if max(int(daily_ts), int(four_ts)) > timestamp:
            raise RuntimeError("future informative timestamp generated")
        output.append(
            {
                "observed_time_ns": int(timestamp),
                "daily_observed_time_ns": int(daily_ts),
                "pair_4h_observed_time_ns": int(four_ts),
                "daily_ema_200": float(daily_value),
                "pair_4h_is_bull": int(four_bull),
                "pair_4h_adx": float(adx_value),
                "ready": True,
            }
        )
    if not output:
        raise RuntimeError(f"no aligned informative rows for {symbol}")
    raw_digest = hashlib.sha256(
        json.dumps(
            {
                "daily_sha256": [item["sha256"] for item in daily_evidence],
                "four_hour_sha256": [item["sha256"] for item in four_evidence],
            },
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return output, {
        "daily_rows": len(daily_raw),
        "four_hour_rows": len(four_raw),
        "aligned_rows": len(output),
        "raw_manifest_sha256": raw_digest,
        "daily_seed_start": str(daily_start),
        "four_hour_seed_start": str(four_hour_start),
        "daily_archives": daily_evidence,
        "four_hour_archives": four_evidence,
    }


def build_sidecar(path: str | Path, start: date, end: date) -> Path:
    destination = Path(path)
    raw_cache = destination.parent / "raw"
    symbols: dict[str, Any] = {}
    metadata: dict[str, Any] = {
        "source": BASE_URL,
        "source_semantics": "checksum-verified completed Binance USD-M monthly 4h and 1d klines",
        "start": str(start),
        "end": str(end),
        "future_information_used": False,
        "daily_ema_period": 200,
        "pair_4h_ema_fast": 50,
        "pair_4h_ema_slow": 200,
        "pair_4h_adx_period": 14,
        "symbols": {},
    }
    for symbol in SYMBOLS:
        rows, record = _source_rows(symbol, start, end, raw_cache)
        symbols[symbol] = rows
        metadata["symbols"][symbol] = record
    payload = {
        "schema_version": 2,
        "metadata": metadata,
        "symbols": symbols,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    MTFContextStore(destination)
    return destination


__all__ = [
    "BASE_URL",
    "MTFContextStore",
    "MTFObservation",
    "SYMBOLS",
    "build_sidecar",
    "configure_context",
    "context_observation",
    "fetch_klines",
]
