"""Deterministic Binance Vision loader for candidate 01.

Only public USD-M perpetual one-minute kline archives are used.  The raw files
remain outside Git and are tied to each run by URL, byte count, SHA-256, and the
publisher's companion checksum when available.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pandas as pd

from core import AuctionBar


KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "base_volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]
BASE_URL = "https://data.binance.vision/data/futures/um/monthly/klines"


@dataclass(frozen=True, slots=True)
class DownloadRecord:
    symbol: str
    interval: str
    month: str
    url: str
    checksum_url: str
    path: str
    size_bytes: int
    sha256: str
    publisher_sha256: str | None


class DataError(RuntimeError):
    pass


def _http_get(url: str, *, timeout: int = 60) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "SMC-ICT-4-LIVE candidate-01 reproducible-research/1.0",
            "Accept": "*/*",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise DataError(f"failed to download {url}: {exc}") from exc


def _month_starts(start: datetime, end: datetime) -> list[datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    current = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    months: list[datetime] = []
    while current < end:
        months.append(current)
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            current = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)
    return months


def _timestamp_unit(series: pd.Series) -> str:
    numeric = pd.to_numeric(series, errors="raise")
    value = int(numeric.dropna().iloc[len(numeric.dropna()) // 2])
    magnitude = abs(value)
    if magnitude >= 10**17:
        return "ns"
    if magnitude >= 10**14:
        return "us"
    if magnitude >= 10**11:
        return "ms"
    if magnitude >= 10**8:
        return "s"
    raise DataError(f"unrecognized timestamp magnitude: {value}")


def _read_archive(payload: bytes, expected_name: str) -> pd.DataFrame:
    try:
        with ZipFile(BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if not names:
                raise DataError("archive contains no files")
            selected = expected_name if expected_name in names else names[0]
            with archive.open(selected) as stream:
                frame = pd.read_csv(stream, header=None, dtype=str)
    except DataError:
        raise
    except Exception as exc:
        raise DataError(f"invalid Binance Vision archive {expected_name}: {exc}") from exc

    if frame.shape[1] < len(KLINE_COLUMNS):
        raise DataError(
            f"expected at least {len(KLINE_COLUMNS)} columns, got {frame.shape[1]}",
        )
    frame = frame.iloc[:, : len(KLINE_COLUMNS)].copy()
    frame.columns = KLINE_COLUMNS

    # Some archives include a textual header.  Drop it only when the timestamp
    # is demonstrably non-numeric; never silently coerce malformed data rows.
    first = str(frame.iloc[0]["open_time"])
    if not re.fullmatch(r"\d+", first):
        frame = frame.iloc[1:].reset_index(drop=True)
    if frame.empty:
        raise DataError("archive contains no kline rows")

    numeric_columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "base_volume",
        "close_time",
        "quote_volume",
        "trade_count",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")

    open_unit = _timestamp_unit(frame["open_time"])
    close_unit = _timestamp_unit(frame["close_time"])
    frame["open_dt"] = pd.to_datetime(frame["open_time"], unit=open_unit, utc=True)
    frame["close_dt"] = pd.to_datetime(frame["close_time"], unit=close_unit, utc=True)
    frame = frame.sort_values("close_dt", kind="stable").drop_duplicates("close_dt", keep="last")
    frame = frame.reset_index(drop=True)

    bad_ohlc = (
        (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
        | (frame["high"] < frame["low"])
    )
    if bool(bad_ohlc.any()):
        raise DataError(f"found {int(bad_ohlc.sum())} inconsistent OHLC rows")
    bad_flow = (
        (frame["quote_volume"] < 0)
        | (frame["taker_buy_quote_volume"] < 0)
        | (frame["taker_buy_quote_volume"] > frame["quote_volume"] * 1.000001)
    )
    if bool(bad_flow.any()):
        raise DataError(f"found {int(bad_flow.sum())} inconsistent flow rows")
    return frame


def _publisher_checksum(text: str, archive_name: str) -> str | None:
    matches = re.findall(r"\b([a-fA-F0-9]{64})\b(?:\s+\*?([^\s]+))?", text)
    if not matches:
        return None
    for digest, name in matches:
        if not name or Path(name).name == archive_name:
            return digest.lower()
    return matches[0][0].lower()


def download_month(
    *,
    symbol: str,
    interval: str,
    month: datetime,
    cache_dir: Path,
    verify_publisher_checksum: bool = True,
) -> tuple[pd.DataFrame, DownloadRecord]:
    symbol = symbol.upper()
    month_text = month.strftime("%Y-%m")
    archive_name = f"{symbol}-{interval}-{month_text}.zip"
    csv_name = f"{symbol}-{interval}-{month_text}.csv"
    url = f"{BASE_URL}/{symbol}/{interval}/{archive_name}"
    checksum_url = f"{url}.CHECKSUM"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / archive_name

    if destination.exists():
        payload = destination.read_bytes()
    else:
        payload = _http_get(url)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)

    local_digest = sha256(payload).hexdigest()
    publisher_digest: str | None = None
    if verify_publisher_checksum:
        try:
            checksum_text = _http_get(checksum_url).decode("utf-8", errors="strict")
            publisher_digest = _publisher_checksum(checksum_text, archive_name)
        except DataError:
            # The local SHA-256 remains in the run manifest even if the optional
            # companion object is temporarily unavailable.
            publisher_digest = None
        if publisher_digest is not None and publisher_digest != local_digest:
            raise DataError(
                f"checksum mismatch for {archive_name}: "
                f"publisher={publisher_digest}, local={local_digest}",
            )

    frame = _read_archive(payload, csv_name)
    record = DownloadRecord(
        symbol=symbol,
        interval=interval,
        month=month_text,
        url=url,
        checksum_url=checksum_url,
        path=str(destination),
        size_bytes=len(payload),
        sha256=local_digest,
        publisher_sha256=publisher_digest,
    )
    return frame, record


def load_interval(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    warmup_minutes: int,
    interval: str = "1m",
) -> tuple[pd.DataFrame, list[DownloadRecord]]:
    """Load [start, end) plus causal warm-up rows before start."""

    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if end <= start:
        raise ValueError("end must be after start")
    fetch_start = start - timedelta(minutes=warmup_minutes)

    frames: list[pd.DataFrame] = []
    records: list[DownloadRecord] = []
    for month in _month_starts(fetch_start, end):
        frame, record = download_month(
            symbol=symbol,
            interval=interval,
            month=month,
            cache_dir=cache_dir,
        )
        frames.append(frame)
        records.append(record)

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("close_dt", kind="stable").drop_duplicates("close_dt", keep="last")
    combined = combined.loc[
        (combined["close_dt"] >= fetch_start)
        & (combined["close_dt"] < end),
    ].reset_index(drop=True)
    if combined.empty:
        raise DataError(f"no rows found for {symbol} in [{fetch_start}, {end})")

    deltas = combined["close_dt"].diff().dropna()
    gaps = deltas[deltas > pd.Timedelta(seconds=61)]
    if not gaps.empty:
        first_gap_index = int(gaps.index[0])
        before = combined.loc[first_gap_index - 1, "close_dt"]
        after = combined.loc[first_gap_index, "close_dt"]
        raise DataError(f"minute continuity gap detected: {before} -> {after}")

    combined["in_evaluation"] = combined["close_dt"] >= start
    return combined, records


def to_auction_bars(frame: pd.DataFrame) -> list[AuctionBar]:
    bars: list[AuctionBar] = []
    for row in frame.itertuples(index=False):
        ts_ns = int(pd.Timestamp(row.close_dt).value)
        bars.append(
            AuctionBar(
                ts_event_ns=ts_ns,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                base_volume=float(row.base_volume),
                quote_volume=float(row.quote_volume),
                taker_buy_quote_volume=float(row.taker_buy_quote_volume),
            ),
        )
    return bars


def write_download_manifest(path: Path, records: Iterable[DownloadRecord]) -> Path:
    payload = {
        "provider": "Binance Vision",
        "market": "USD-M perpetual futures",
        "records": [asdict(record) for record in records],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def parse_utc_date(value: str) -> datetime:
    parsed = date.fromisoformat(value)
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)
