"""Causal official Binance L1 data adapters for candidate 10."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import csv
import io
import os
from pathlib import Path
import shutil
import struct
import time
from typing import Any, Iterable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import zipfile

BOOKTICKER_ROOT = "https://data.binance.vision/data/futures/um/daily/bookTicker"
ALIGNMENT_SCHEMA_VERSION = 1
# trade_id, trade_ts, trade_px, trade_qty, aggressor,
# quote_update_id, quote_event_ts, bid_px, bid_qty, ask_px, ask_qty
ALIGNMENT_RECORD = struct.Struct("<qqddbqqdddd")
REPLAY_BATCH_EVENTS = 200_000


@dataclass(frozen=True, slots=True)
class RawQuote:
    update_id: int
    ts_ns: int
    bid: str
    bid_size: str
    ask: str
    ask_size: str


@dataclass(frozen=True, slots=True)
class RawTrade:
    trade_id: int
    ts_ns: int
    price: str
    quantity: str
    aggressor: int


@dataclass(frozen=True, slots=True)
class AlignedRecord:
    trade: RawTrade
    quote: RawQuote | None


def align_latest_known_quotes(
    quotes: Iterator[RawQuote],
    trades: Iterator[RawTrade],
) -> Iterator[AlignedRecord]:
    """Pair each trade only with the latest quote already observable at its time."""

    next_quote = next(quotes, None)
    latest_quote: RawQuote | None = None
    for trade in trades:
        while next_quote is not None and next_quote.ts_ns <= trade.ts_ns:
            latest_quote = next_quote
            next_quote = next(quotes, None)
        if latest_quote is not None and latest_quote.ts_ns > trade.ts_ns:
            raise RuntimeError(
                "causal alignment violated: "
                f"quote={latest_quote.ts_ns} trade={trade.ts_ns}",
            )
        yield AlignedRecord(trade=trade, quote=latest_quote)


def _download_file(url: str, destination: Path, attempts: int = 4) -> None:
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    error: Exception | None = None
    for attempt in range(attempts):
        temporary = destination.with_suffix(destination.suffix + ".partial")
        try:
            if temporary.exists():
                temporary.unlink()
            with urlopen(url, timeout=180) as response, temporary.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=8 * 1024 * 1024)
            os.replace(temporary, destination)
            return
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            error = exc
            if temporary.exists():
                temporary.unlink()
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {error}")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(8 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def download_binance_bookticker_week(
    week_start: date,
    destination: str | Path,
    *,
    symbol: str = "BTCUSDT",
    warmup_days: int = 1,
) -> tuple[list[Path], dict[str, Any]]:
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    first = week_start - timedelta(days=warmup_days)
    last_exclusive = week_start + timedelta(days=7)
    paths: list[Path] = []
    records: list[dict[str, Any]] = []
    cursor = first
    while cursor < last_exclusive:
        stem = f"{symbol}-bookTicker-{cursor.isoformat()}"
        base = f"{BOOKTICKER_ROOT}/{symbol}/{stem}.zip"
        archive = root / f"{stem}.zip"
        checksum = root / f"{stem}.zip.CHECKSUM"
        _download_file(base + ".CHECKSUM", checksum)
        _download_file(base, archive)
        expected = checksum.read_text(encoding="utf-8").strip().split()[0]
        actual = _sha256_file(archive)
        if actual.lower() != expected.lower():
            raise RuntimeError(
                f"checksum mismatch for {archive.name}: {actual} != {expected}",
            )
        paths.extend([archive, checksum])
        records.append(
            {
                "date": cursor.isoformat(),
                "zip": archive.name,
                "sha256": actual,
                "archive_bytes": archive.stat().st_size,
            },
        )
        cursor += timedelta(days=1)
    return paths, {
        "provider": "Binance public data",
        "market": "USD-M futures",
        "dataset": "best bid/ask bookTicker",
        "symbol": symbol,
        "week_start": week_start.isoformat(),
        "warmup_days": warmup_days,
        "timestamp_used": "event_time",
        "files": records,
    }


def _iter_zip_rows(path: Path) -> Iterator[list[str]]:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.endswith(".csv")]
        if len(members) != 1:
            raise RuntimeError(f"expected one CSV in {path}, found {members}")
        stream = io.TextIOWrapper(archive.open(members[0]), encoding="utf-8")
        for raw in csv.reader(stream):
            if raw:
                yield [item.strip() for item in raw]


def _timestamp_to_ns(raw: str) -> int:
    value = int(raw)
    if value < 10_000_000_000_000:
        return value * 1_000_000
    if value < 10_000_000_000_000_000:
        return value * 1_000
    return value


def _bool_field(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ValueError(f"unexpected boolean field: {raw!r}")


def _iter_raw_quotes(path: Path, diagnostics: Counter[str]) -> Iterator[RawQuote]:
    previous_id: int | None = None
    previous_ts: int | None = None
    for row in _iter_zip_rows(path):
        if not row[0].lstrip("-").isdigit():
            continue
        if len(row) != 7:
            raise RuntimeError(f"unexpected bookTicker width {len(row)} in {path}")
        update_id = int(row[0])
        ts_ns = _timestamp_to_ns(row[6])
        diagnostics["quote_rows"] += 1
        if previous_id is not None:
            if update_id == previous_id:
                diagnostics["duplicate_quote_update_ids"] += 1
            elif update_id < previous_id:
                diagnostics["nonmonotonic_quote_update_ids"] += 1
        if previous_ts is not None and ts_ns < previous_ts:
            diagnostics["nonmonotonic_quote_event_times"] += 1
        previous_id = update_id
        previous_ts = ts_ns
        yield RawQuote(
            update_id=update_id,
            ts_ns=ts_ns,
            bid=row[1],
            bid_size=row[2],
            ask=row[3],
            ask_size=row[4],
        )


def _iter_raw_trades(path: Path, diagnostics: Counter[str]) -> Iterator[RawTrade]:
    previous_id: int | None = None
    previous_ts: int | None = None
    for row in _iter_zip_rows(path):
        if not row[0].lstrip("-").isdigit():
            continue
        if len(row) not in {7, 8}:
            raise RuntimeError(f"unexpected aggregate-trade width {len(row)} in {path}")
        trade_id = int(row[0])
        ts_ns = _timestamp_to_ns(row[5])
        buyer_maker = _bool_field(row[6])
        diagnostics["trade_rows"] += 1
        if previous_id is not None:
            if trade_id == previous_id:
                diagnostics["duplicate_trade_ids"] += 1
            elif trade_id < previous_id:
                diagnostics["nonmonotonic_trade_ids"] += 1
        if previous_ts is not None and ts_ns < previous_ts:
            diagnostics["nonmonotonic_trade_times"] += 1
        previous_id = trade_id
        previous_ts = ts_ns
        yield RawTrade(
            trade_id=trade_id,
            ts_ns=ts_ns,
            price=row[1],
            quantity=row[2],
            aggressor=-1 if buyer_maker else 1,
        )


def _archive_by_date(paths: Iterable[Path], token: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    marker = f"-{token}-"
    for path in paths:
        if path.suffix != ".zip" or marker not in path.name:
            continue
        value = path.stem.rsplit("-", 3)
        if len(value) < 3:
            continue
        day = "-".join(value[-3:])
        result[day] = path
    return result


def _source_sha_by_date(metadata: dict[str, Any]) -> dict[str, str]:
    return {
        str(record["date"]): str(record["sha256"])
        for record in metadata.get("files", [])
    }

__all__ = [
    "ALIGNMENT_RECORD",
    "ALIGNMENT_SCHEMA_VERSION",
    "AlignedRecord",
    "RawQuote",
    "RawTrade",
    "_archive_by_date",
    "_iter_raw_quotes",
    "_iter_raw_trades",
    "_sha256_file",
    "_source_sha_by_date",
    "align_latest_known_quotes",
    "download_binance_bookticker_week",
]
