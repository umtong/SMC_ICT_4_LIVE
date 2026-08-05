"""Causal loader for official Binance Vision futures positioning data."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile

import numpy as np
import pandas as pd


BASE = "https://data.binance.vision/data/futures/um/daily"


@dataclass(frozen=True, slots=True)
class AuxiliaryDownload:
    data_type: str
    symbol: str
    day: str
    url: str
    path: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _days(start: datetime, end: datetime) -> list[date]:
    current = start.astimezone(timezone.utc).date()
    final = end.astimezone(timezone.utc).date()
    result: list[date] = []
    while current <= final:
        result.append(current)
        current += timedelta(days=1)
    return result


def _url(data_type: str, symbol: str, day: date) -> str:
    value = day.isoformat()
    if data_type == "metrics":
        return f"{BASE}/metrics/{symbol}/{symbol}-metrics-{value}.zip"
    if data_type == "premiumIndexKlines":
        return f"{BASE}/premiumIndexKlines/{symbol}/1m/{symbol}-1m-{value}.zip"
    if data_type == "markPriceKlines":
        return f"{BASE}/markPriceKlines/{symbol}/1m/{symbol}-1m-{value}.zip"
    if data_type == "bookDepth":
        return f"{BASE}/bookDepth/{symbol}/{symbol}-bookDepth-{value}.zip"
    raise ValueError(f"unsupported auxiliary data type: {data_type}")


def _download_one(
    *,
    data_type: str,
    symbol: str,
    day: date,
    cache_dir: Path,
    retries: int = 3,
) -> AuxiliaryDownload:
    url = _url(data_type, symbol, day)
    destination = cache_dir / data_type / symbol / f"{day.isoformat()}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file() or destination.stat().st_size == 0:
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                request = Request(url, headers={"User-Agent": "smc-ict-4-research/1.0"})
                with urlopen(request, timeout=60) as response:
                    payload = response.read()
                if not payload:
                    raise OSError(f"empty response from {url}")
                temporary = destination.with_suffix(".zip.tmp")
                temporary.write_bytes(payload)
                temporary.replace(destination)
                break
            except (HTTPError, URLError, OSError) as exc:
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(1.0 * (attempt + 1))
        else:
            assert last_error is not None
            raise last_error
    payload = destination.read_bytes()
    return AuxiliaryDownload(
        data_type=data_type,
        symbol=symbol,
        day=day.isoformat(),
        url=url,
        path=str(destination),
        size_bytes=len(payload),
        sha256=sha256(payload).hexdigest(),
    )


def download_auxiliary(
    *,
    data_types: tuple[str, ...],
    symbol: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    workers: int = 16,
) -> list[AuxiliaryDownload]:
    requests = [
        (data_type, day)
        for data_type in data_types
        for day in _days(start, end)
    ]
    records: list[AuxiliaryDownload] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                _download_one,
                data_type=data_type,
                symbol=symbol,
                day=day,
                cache_dir=cache_dir,
            ): (data_type, day)
            for data_type, day in requests
        }
        for future in as_completed(futures):
            records.append(future.result())
    return sorted(records, key=lambda item: (item.data_type, item.day))


def _member_csv(payload: bytes) -> bytes:
    with ZipFile(BytesIO(payload)) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV member, found {members}")
        return archive.read(members[0])


def read_metrics(records: list[AuxiliaryDownload]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for record in records:
        if record.data_type != "metrics":
            continue
        frame = pd.read_csv(BytesIO(_member_csv(Path(record.path).read_bytes())))
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    result["create_time"] = pd.to_datetime(result["create_time"], utc=True)
    numeric = [column for column in result.columns if column not in {"create_time", "symbol"}]
    result[numeric] = result[numeric].apply(pd.to_numeric, errors="coerce")
    result = result.sort_values("create_time", kind="stable").drop_duplicates("create_time", keep="last")
    result = result.reset_index(drop=True)

    oi = result["sum_open_interest"]
    result["oi_pct_5"] = oi.pct_change(1)
    result["oi_pct_15"] = oi.pct_change(3)
    result["oi_pct_30"] = oi.pct_change(6)
    result["oi_pct_60"] = oi.pct_change(12)
    for source in ("oi_pct_5", "oi_pct_15", "oi_pct_30"):
        history = result[source].rolling(72, min_periods=24)
        result[f"{source}_z"] = (
            result[source] - history.mean().shift(1)
        ) / history.std(ddof=0).shift(1).replace(0.0, np.nan)
    for source in (
        "count_toptrader_long_short_ratio",
        "sum_toptrader_long_short_ratio",
        "count_long_short_ratio",
        "sum_taker_long_short_vol_ratio",
    ):
        logged = np.log(result[source].where(result[source] > 0.0))
        result[f"log_{source}"] = logged
        history = logged.rolling(72, min_periods=24)
        result[f"{source}_z"] = (
            logged - history.mean().shift(1)
        ) / history.std(ddof=0).shift(1).replace(0.0, np.nan)
    return result


def read_index_klines(
    records: list[AuxiliaryDownload],
    *,
    data_type: str,
    prefix: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for record in records:
        if record.data_type != data_type:
            continue
        frame = pd.read_csv(BytesIO(_member_csv(Path(record.path).read_bytes())))
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    result["close_time"] = pd.to_datetime(
        pd.to_numeric(result["close_time"], errors="raise"),
        unit="ms",
        utc=True,
    )
    for column in ("open", "high", "low", "close"):
        result[f"{prefix}_{column}"] = pd.to_numeric(result[column], errors="coerce")
    result = result[["close_time", *[f"{prefix}_{column}" for column in ("open", "high", "low", "close")]]]
    result = result.sort_values("close_time", kind="stable").drop_duplicates("close_time", keep="last")
    close = result[f"{prefix}_close"]
    for window in (60, 120, 360):
        history = close.rolling(window, min_periods=max(30, window // 3))
        result[f"{prefix}_z_{window}"] = (
            close - history.mean().shift(1)
        ) / history.std(ddof=0).shift(1).replace(0.0, np.nan)
    return result.reset_index(drop=True)


def merge_auxiliary_at_times(
    event_times_ns: pd.Series,
    *,
    metrics: pd.DataFrame,
    premium: pd.DataFrame,
    mark: pd.DataFrame | None = None,
) -> pd.DataFrame:
    events = pd.DataFrame(
        {
            "event_row": np.arange(len(event_times_ns), dtype=int),
            "event_time": pd.to_datetime(event_times_ns.astype("int64"), unit="ns", utc=True),
        },
    ).sort_values("event_time", kind="stable")
    if not metrics.empty:
        events = pd.merge_asof(
            events,
            metrics.sort_values("create_time", kind="stable"),
            left_on="event_time",
            right_on="create_time",
            direction="backward",
            allow_exact_matches=True,
        )
    if not premium.empty:
        events = pd.merge_asof(
            events.sort_values("event_time", kind="stable"),
            premium.sort_values("close_time", kind="stable"),
            left_on="event_time",
            right_on="close_time",
            direction="backward",
            allow_exact_matches=True,
            suffixes=("", "_premium"),
        )
    if mark is not None and not mark.empty:
        events = pd.merge_asof(
            events.sort_values("event_time", kind="stable"),
            mark.sort_values("close_time", kind="stable"),
            left_on="event_time",
            right_on="close_time",
            direction="backward",
            allow_exact_matches=True,
            suffixes=("", "_mark"),
        )
    return events.sort_values("event_row", kind="stable").reset_index(drop=True)
