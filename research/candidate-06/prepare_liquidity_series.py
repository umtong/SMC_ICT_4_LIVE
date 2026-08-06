"""Normalize official Binance Vision passive-liquidity archives causally.

Preferred source is USD-M ``bookDepth`` (notional at fixed percentage bands).
When that source does not exist for the requested market, the implementation
falls back to USD-M ``bookTicker`` and records the final observed inside-quote
notional for each completed minute.  The source kind is preserved explicitly;
top-of-book liquidity is never mislabeled as multi-level depth, and OHLCV is
never substituted for missing passive-liquidity data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import time
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator


BASE = "https://data.binance.vision/data/futures/um/daily"
USER_AGENT = "candidate-06-research/1.1"
MINUTE_NS = 60_000_000_000


class LiquidityArchiveUnavailable(RuntimeError):
    """Raised only when both official source routes return HTTP 404."""


class LiquidityPreparationError(RuntimeError):
    """Raised for corrupt, incomplete, or temporally inconsistent data."""


@dataclass(frozen=True, slots=True)
class PreparedLiquidity:
    path: Path
    source: str
    measurement: str
    source_files: tuple[str, ...]
    rows: int
    first_ts_ns: int
    last_ts_ns: int
    max_gap_minutes: float


def _days(start: date, end: date) -> Iterable[date]:
    current = start
    while current < end:
        yield current
        current += timedelta(days=1)


def _date_ns(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def _to_ns(value: int) -> int:
    absolute = abs(value)
    if absolute < 10_000_000_000:
        return value * 1_000_000_000
    if absolute < 10_000_000_000_000:
        return value * 1_000_000
    if absolute < 10_000_000_000_000_000:
        return value * 1_000
    return value


def _parse_timestamp_ns(raw: str) -> int:
    """Parse Binance Vision numeric or ISO-8601 timestamps causally.

    Historical ``bookDepth`` partitions are not schema-stable across vintages:
    some encode POSIX seconds while others encode a UTC datetime string.  This
    adapter accepts only those two documented representations and never infers
    time from row order or neighboring observations.
    """
    value = str(raw).strip()
    if not value:
        raise ValueError("empty timestamp")
    try:
        return _to_ns(int(float(value)))
    except ValueError:
        pass

    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def _download(url: str) -> bytes:
    delays = (0.0, 2.0, 5.0, 10.0)
    last_error: BaseException | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
    raise LiquidityPreparationError(f"download failed after retries: {url}: {last_error}")


def _download_verified(url: str) -> bytes:
    payload = _download(url)
    try:
        checksum_text = _download(url + ".CHECKSUM").decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return payload
        raise
    expected = checksum_text.strip().split()[0]
    actual = hashlib.sha256(payload).hexdigest()
    if expected and actual != expected:
        raise LiquidityPreparationError(
            f"checksum mismatch for {url}: expected={expected} actual={actual}",
        )
    return payload


def _csv_rows(payload: bytes) -> Iterator[list[str]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise LiquidityPreparationError(f"expected one CSV member, got {names}")
        with archive.open(names[0]) as source:
            reader = csv.reader(io.TextIOWrapper(source, encoding="utf-8"))
            yield from reader


def _header_and_rows(payload: bytes) -> tuple[list[str], Iterator[list[str]]]:
    rows = _csv_rows(payload)
    try:
        first = next(rows)
    except StopIteration as exc:
        raise LiquidityPreparationError("empty CSV archive member") from exc
    header = [item.strip().lower() for item in first]
    first_is_header = any(any(character.isalpha() for character in item) for item in first)
    return (
        header if first_is_header else [],
        rows if first_is_header else itertools.chain([first], rows),
    )


def _index(header: list[str], names: tuple[str, ...], fallback: int) -> int:
    for candidate in names:
        if candidate in header:
            return header.index(candidate)
    return fallback


def _quality(
    timestamps: list[int],
    *,
    start: date,
    end: date,
    source: str,
) -> tuple[int, int, float]:
    if not timestamps:
        raise LiquidityPreparationError(f"{source} produced no normalized records")
    if timestamps != sorted(timestamps):
        raise LiquidityPreparationError(f"{source} normalized timestamps are not monotonic")
    if len(set(timestamps)) != len(timestamps):
        raise LiquidityPreparationError(f"{source} normalized timestamps contain duplicates")

    first = timestamps[0]
    last = timestamps[-1]
    expected_start = _date_ns(start)
    expected_end = _date_ns(end)
    boundary_tolerance = 30 * MINUTE_NS
    if first > expected_start + boundary_tolerance:
        raise LiquidityPreparationError(
            f"{source} starts too late: first={first} expected_start={expected_start}",
        )
    if last < expected_end - boundary_tolerance:
        raise LiquidityPreparationError(
            f"{source} ends too early: last={last} expected_end={expected_end}",
        )
    max_gap = max((right - left for left, right in zip(timestamps, timestamps[1:])), default=0)
    if max_gap > 60 * MINUTE_NS:
        raise LiquidityPreparationError(
            f"{source} has passive-liquidity gap of {max_gap / MINUTE_NS:.2f} minutes",
        )
    return first, last, max_gap / MINUTE_NS


def _try_book_depth(
    symbol: str,
    start: date,
    end: date,
    output: Path,
) -> PreparedLiquidity | None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".bookDepth.tmp")
    temporary.unlink(missing_ok=True)
    rows_written = 0
    source_files: list[str] = []
    timestamps: list[int] = []

    try:
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["ts_ns", "bid_near", "ask_near", "bid_total", "ask_total"],
            )
            writer.writeheader()
            for day in _days(start, end):
                text = day.isoformat()
                url = f"{BASE}/bookDepth/{symbol}/{symbol}-bookDepth-{text}.zip"
                try:
                    payload = _download_verified(url)
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        temporary.unlink(missing_ok=True)
                        return None
                    raise
                header, data_rows = _header_and_rows(payload)
                ts_index = _index(
                    header,
                    ("timestamp", "time", "event_time", "transact_time"),
                    0,
                )
                percentage_index = _index(
                    header,
                    ("percentage", "percent", "price_percentage"),
                    1,
                )
                notional_index = _index(
                    header,
                    ("notional", "quote", "quote_notional"),
                    3,
                )
                grouped: dict[int, list[tuple[float, float]]] = defaultdict(list)
                for row in data_rows:
                    if len(row) <= max(ts_index, percentage_index, notional_index):
                        continue
                    try:
                        timestamp = _parse_timestamp_ns(row[ts_index])
                        percentage = float(row[percentage_index])
                        notional = float(row[notional_index])
                    except (TypeError, ValueError):
                        continue
                    if notional < 0.0 or percentage == 0.0:
                        continue
                    grouped[timestamp].append((percentage, notional))

                for timestamp in sorted(grouped):
                    values = grouped[timestamp]
                    bids = sorted(
                        (abs(percentage), notional)
                        for percentage, notional in values
                        if percentage < 0.0
                    )
                    asks = sorted(
                        (abs(percentage), notional)
                        for percentage, notional in values
                        if percentage > 0.0
                    )
                    if not bids or not asks:
                        continue
                    writer.writerow(
                        {
                            "ts_ns": timestamp,
                            "bid_near": sum(notional for _, notional in bids[:2]),
                            "ask_near": sum(notional for _, notional in asks[:2]),
                            "bid_total": sum(notional for _, notional in bids),
                            "ask_total": sum(notional for _, notional in asks),
                        },
                    )
                    timestamps.append(timestamp)
                    rows_written += 1
                source_files.append(url)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    first, last, max_gap = _quality(timestamps, start=start, end=end, source="bookDepth")
    temporary.replace(output)
    return PreparedLiquidity(
        path=output,
        source="bookDepth",
        measurement="fixed-percentage-band passive notional",
        source_files=tuple(source_files),
        rows=rows_written,
        first_ts_ns=first,
        last_ts_ns=last,
        max_gap_minutes=max_gap,
    )


def _try_book_ticker(
    symbol: str,
    start: date,
    end: date,
    output: Path,
) -> PreparedLiquidity | None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".bookTicker.tmp")
    temporary.unlink(missing_ok=True)
    rows_written = 0
    source_files: list[str] = []
    timestamps: list[int] = []

    try:
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["ts_ns", "bid_near", "ask_near", "bid_total", "ask_total"],
            )
            writer.writeheader()
            for day in _days(start, end):
                text = day.isoformat()
                url = f"{BASE}/bookTicker/{symbol}/{symbol}-bookTicker-{text}.zip"
                try:
                    payload = _download_verified(url)
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        temporary.unlink(missing_ok=True)
                        return None
                    raise
                header, data_rows = _header_and_rows(payload)
                bid_price_index = _index(
                    header,
                    ("best_bid_price", "bid_price", "bidprice"),
                    1,
                )
                bid_qty_index = _index(
                    header,
                    ("best_bid_qty", "bid_qty", "bidqty"),
                    2,
                )
                ask_price_index = _index(
                    header,
                    ("best_ask_price", "ask_price", "askprice"),
                    3,
                )
                ask_qty_index = _index(
                    header,
                    ("best_ask_qty", "ask_qty", "askqty"),
                    4,
                )
                timestamp_index = _index(
                    header,
                    ("event_time", "transaction_time", "time", "timestamp"),
                    6,
                )
                minute_last: dict[int, tuple[int, float, float]] = {}
                for row in data_rows:
                    if len(row) <= max(
                        bid_price_index,
                        bid_qty_index,
                        ask_price_index,
                        ask_qty_index,
                        timestamp_index,
                    ):
                        continue
                    try:
                        timestamp = _to_ns(int(float(row[timestamp_index])))
                        bid_notional = float(row[bid_price_index]) * float(row[bid_qty_index])
                        ask_notional = float(row[ask_price_index]) * float(row[ask_qty_index])
                    except (TypeError, ValueError):
                        continue
                    if min(bid_notional, ask_notional) < 0.0:
                        continue
                    minute = timestamp // MINUTE_NS
                    current = minute_last.get(minute)
                    if current is None or timestamp > current[0]:
                        minute_last[minute] = (timestamp, bid_notional, ask_notional)

                for minute in sorted(minute_last):
                    timestamp, bid_notional, ask_notional = minute_last[minute]
                    writer.writerow(
                        {
                            "ts_ns": timestamp,
                            "bid_near": bid_notional,
                            "ask_near": ask_notional,
                            "bid_total": bid_notional,
                            "ask_total": ask_notional,
                        },
                    )
                    timestamps.append(timestamp)
                    rows_written += 1
                source_files.append(url)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    first, last, max_gap = _quality(timestamps, start=start, end=end, source="bookTicker")
    temporary.replace(output)
    return PreparedLiquidity(
        path=output,
        source="bookTicker",
        measurement="last observed inside-quote passive notional per completed minute",
        source_files=tuple(source_files),
        rows=rows_written,
        first_ts_ns=first,
        last_ts_ns=last,
        max_gap_minutes=max_gap,
    )


def prepare(symbol: str, start: date, end: date, output: Path) -> PreparedLiquidity:
    """Prepare one frozen interval from official data or fail without proxying."""

    if end <= start:
        raise ValueError("end must be after start")
    prepared = _try_book_depth(symbol, start, end, output)
    if prepared is None:
        prepared = _try_book_ticker(symbol, start, end, output)
    if prepared is None:
        raise LiquidityArchiveUnavailable(
            f"no official Binance USD-M passive-liquidity archive for {symbol} {start}..{end}",
        )

    manifest = {
        "source": f"Binance Vision USD-M futures {prepared.source}",
        "measurement": prepared.measurement,
        "symbol": symbol,
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "records": prepared.rows,
        "first_ts_ns": prepared.first_ts_ns,
        "last_ts_ns": prepared.last_ts_ns,
        "max_gap_minutes": prepared.max_gap_minutes,
        "source_files": list(prepared.source_files),
        "timestamp_contract": (
            "latest official passive-liquidity observation with timestamp at or before "
            "the completed one-minute bar event; feature normalization uses only prior "
            "passive-liquidity observations"
        ),
        "synthetic_depth": False,
        "ohlcv_substitution": False,
        "checksum_policy": "verify SHA-256 when Binance Vision CHECKSUM is available",
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return prepared
