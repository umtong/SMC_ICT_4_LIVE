"""Verified streaming loader for official Binance USD-M futures aggTrades.

The publisher exposes one ZIP and one SHA-256 CHECKSUM file per UTC day. This
module downloads both, verifies the archive before use, normalizes optional
headers and timestamp units, and yields immutable aggregate-trade events in
strict event order. It intentionally does not convert the event stream into
candles; callers may build causal volume/information clocks without losing
aggressor direction.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from io import TextIOWrapper
from pathlib import Path
import re
import shutil
import time
from typing import Iterable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile


BASE = "https://data.binance.vision/data/futures/um/daily/aggTrades"
COLUMNS = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)


@dataclass(frozen=True, slots=True)
class AggTradeDownload:
    symbol: str
    day: str
    url: str
    checksum_url: str
    path: str
    checksum_path: str
    size_bytes: int
    sha256: str
    expected_sha256: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AggTrade:
    agg_trade_id: int
    price: float
    quantity: float
    first_trade_id: int
    last_trade_id: int
    ts_event_ns: int
    is_buyer_maker: bool

    @property
    def quote_notional(self) -> float:
        return self.price * self.quantity

    @property
    def signed_aggressive_quote(self) -> float:
        # Buyer-maker means the aggressive order was a sell.
        return -self.quote_notional if self.is_buyer_maker else self.quote_notional


@dataclass(frozen=True, slots=True)
class AggTradeFileStats:
    day: str
    rows: int
    first_time_ns: int
    last_time_ns: int
    first_agg_trade_id: int
    last_agg_trade_id: int
    total_quote_notional: float
    signed_quote_notional: float
    non_monotonic_timestamps: int
    non_monotonic_ids: int
    duplicate_agg_trade_ids: int
    max_inter_event_gap_ms: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def utc_days(start: datetime, end: datetime) -> list[date]:
    """Return UTC days intersecting [start, end)."""

    current = start.astimezone(timezone.utc).date()
    final_inclusive = (end - timedelta(microseconds=1)).astimezone(timezone.utc).date()
    result: list[date] = []
    while current <= final_inclusive:
        result.append(current)
        current += timedelta(days=1)
    return result


def archive_url(symbol: str, day: date) -> str:
    value = day.isoformat()
    return f"{BASE}/{symbol}/{symbol}-aggTrades-{value}.zip"


def _request(url: str):
    return Request(url, headers={"User-Agent": "smc-ict-4-research/1.0"})


def _download_to_path(url: str, destination: Path, *, retries: int = 4) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0:
        return
    last_error: Exception | None = None
    for attempt in range(retries):
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            with urlopen(_request(url), timeout=120) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            if temporary.stat().st_size <= 0:
                raise OSError(f"empty response from {url}")
            temporary.replace(destination)
            return
        except (HTTPError, URLError, OSError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _expected_checksum(path: Path, archive_name: str) -> str:
    text = path.read_text(encoding="utf-8").strip()
    match = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    if match is None:
        raise ValueError(f"invalid checksum file {path}: {text!r}")
    if archive_name not in text:
        raise ValueError(f"checksum file {path} does not name {archive_name}")
    return match.group(1).lower()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_one(*, symbol: str, day: date, cache_dir: Path) -> AggTradeDownload:
    url = archive_url(symbol, day)
    archive_name = url.rsplit("/", 1)[-1]
    destination = cache_dir / symbol / archive_name
    checksum_url = url + ".CHECKSUM"
    checksum_path = destination.with_suffix(destination.suffix + ".CHECKSUM")
    _download_to_path(url, destination)
    _download_to_path(checksum_url, checksum_path)
    expected = _expected_checksum(checksum_path, archive_name)
    actual = _sha256_file(destination)
    if actual != expected:
        # Never silently use a stale or partially cached archive.
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


def download_aggtrade_days(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    workers: int = 4,
) -> list[AggTradeDownload]:
    days = utc_days(start, end)
    records: list[AggTradeDownload] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(days)))) as executor:
        futures = {
            executor.submit(
                _download_one,
                symbol=symbol,
                day=day,
                cache_dir=cache_dir,
            ): day
            for day in days
        }
        for future in as_completed(futures):
            records.append(future.result())
    return sorted(records, key=lambda item: item.day)


def _timestamp_to_ns(raw: str) -> int:
    value = int(raw)
    magnitude = abs(value)
    if magnitude >= 10**17:
        return value
    if magnitude >= 10**14:
        return value * 1_000
    if magnitude >= 10**11:
        return value * 1_000_000
    if magnitude >= 10**8:
        return value * 1_000_000_000
    raise ValueError(f"unrecognized timestamp magnitude: {value}")


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"true", "1", "t"}:
        return True
    if value in {"false", "0", "f"}:
        return False
    raise ValueError(f"invalid boolean value: {raw!r}")


def iter_download(record: AggTradeDownload) -> Iterator[AggTrade]:
    path = Path(record.path)
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {path}, found {members}")
        with archive.open(members[0], "r") as raw, TextIOWrapper(
            raw,
            encoding="utf-8-sig",
            newline="",
        ) as text:
            reader = csv.reader(text)
            for row_number, row in enumerate(reader, start=1):
                if not row:
                    continue
                if row_number == 1 and not row[0].strip().lstrip("-").isdigit():
                    continue
                if len(row) < len(COLUMNS):
                    raise ValueError(
                        f"{path}:{row_number}: expected {len(COLUMNS)} columns, got {len(row)}",
                    )
                yield AggTrade(
                    agg_trade_id=int(row[0]),
                    price=float(row[1]),
                    quantity=float(row[2]),
                    first_trade_id=int(row[3]),
                    last_trade_id=int(row[4]),
                    ts_event_ns=_timestamp_to_ns(row[5]),
                    is_buyer_maker=_parse_bool(row[6]),
                )


def iter_downloads(records: Iterable[AggTradeDownload]) -> Iterator[AggTrade]:
    previous_time = -1
    previous_id = -1
    for record in sorted(records, key=lambda item: item.day):
        for trade in iter_download(record):
            if trade.ts_event_ns < previous_time:
                raise ValueError(
                    f"cross-file timestamp regression at {record.day}: "
                    f"{trade.ts_event_ns} < {previous_time}",
                )
            if trade.agg_trade_id <= previous_id:
                raise ValueError(
                    f"cross-file aggregate-trade ID regression at {record.day}: "
                    f"{trade.agg_trade_id} <= {previous_id}",
                )
            previous_time = trade.ts_event_ns
            previous_id = trade.agg_trade_id
            yield trade


def inspect_download(record: AggTradeDownload) -> AggTradeFileStats:
    rows = 0
    first_time = 0
    last_time = 0
    first_id = 0
    last_id = 0
    total_quote = 0.0
    signed_quote = 0.0
    non_monotonic_timestamps = 0
    non_monotonic_ids = 0
    duplicate_ids = 0
    max_gap_ns = 0
    previous_time: int | None = None
    previous_id: int | None = None
    for trade in iter_download(record):
        if rows == 0:
            first_time = trade.ts_event_ns
            first_id = trade.agg_trade_id
        if previous_time is not None:
            if trade.ts_event_ns < previous_time:
                non_monotonic_timestamps += 1
            max_gap_ns = max(max_gap_ns, trade.ts_event_ns - previous_time)
        if previous_id is not None:
            if trade.agg_trade_id < previous_id:
                non_monotonic_ids += 1
            elif trade.agg_trade_id == previous_id:
                duplicate_ids += 1
        rows += 1
        last_time = trade.ts_event_ns
        last_id = trade.agg_trade_id
        total_quote += trade.quote_notional
        signed_quote += trade.signed_aggressive_quote
        previous_time = trade.ts_event_ns
        previous_id = trade.agg_trade_id
    if rows == 0:
        raise ValueError(f"no aggregate trades in {record.path}")
    return AggTradeFileStats(
        day=record.day,
        rows=rows,
        first_time_ns=first_time,
        last_time_ns=last_time,
        first_agg_trade_id=first_id,
        last_agg_trade_id=last_id,
        total_quote_notional=total_quote,
        signed_quote_notional=signed_quote,
        non_monotonic_timestamps=non_monotonic_timestamps,
        non_monotonic_ids=non_monotonic_ids,
        duplicate_agg_trade_ids=duplicate_ids,
        max_inter_event_gap_ms=max_gap_ns / 1_000_000.0,
    )
