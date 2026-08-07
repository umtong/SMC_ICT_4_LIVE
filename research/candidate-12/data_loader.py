"""Immutable Binance Vision one-minute kline loader with causal close timestamps."""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import time
from typing import Any
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pandas as pd

COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)


def _write_raw_events(path: Path, events: list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")
    temporary.replace(path)
    return path


def _download(url: str, destination: Path, retries: int = 4) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 100:
        with ZipFile(destination) as archive:
            if archive.testzip() is None:
                return
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-12/1.0"})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed HTTPS archive host
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError(f"unexpectedly small response from {url}")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(payload)
            with ZipFile(temporary) as archive:
                bad_member = archive.testzip()
                if bad_member is not None:
                    raise RuntimeError(f"corrupt ZIP member {bad_member}")
            temporary.replace(destination)
            return
        except Exception as exc:  # retain the final network/format error
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def load_binance_bars(
    symbol: str,
    start: date,
    end_inclusive: date,
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Load immutable Binance USD-M one-minute klines at causal close time."""
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end_inclusive:
        filename = f"{symbol}-1m-{cursor.isoformat()}.zip"
        url = f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1m/{filename}"
        path = data_dir / symbol / filename
        _download(url, path)
        digest = sha256(path.read_bytes()).hexdigest()
        with ZipFile(path) as archive:
            members = archive.namelist()
            if len(members) != 1 or not members[0].lower().endswith(".csv"):
                raise RuntimeError(f"unexpected ZIP members in {filename}: {members}")
            with archive.open(members[0]) as stream:
                frame = pd.read_csv(stream, header=None, names=COLUMNS)
        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()
        if len(frame.index) not in (1439, 1440, 1441):
            raise RuntimeError(f"unexpected row count {len(frame.index)} for {filename}")
        frames.append(frame)
        manifest.append(
            {
                "symbol": symbol,
                "date": cursor.isoformat(),
                "url": url,
                "file": f"{symbol}/{filename}",
                "bytes": path.stat().st_size,
                "sha256": digest,
                "rows": len(frame.index),
            },
        )
        cursor += timedelta(days=1)

    raw = pd.concat(frames, ignore_index=True)
    raw = raw.drop_duplicates(subset=["open_time"], keep="last")
    raw = raw.sort_values("open_time", kind="stable").reset_index(drop=True)
    open_time = pd.to_numeric(raw["open_time"], errors="raise")
    first = int(open_time.iloc[0])
    if 1_000_000_000_000 <= first < 10_000_000_000_000:
        unit = "ms"
    elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        unit = "us"
    else:
        raise RuntimeError(f"unsupported Binance timestamp magnitude: {first}")
    # Binance labels bars by open time; all OHLC/volume/flow fields become
    # observable only at minute close.
    index = pd.to_datetime(open_time, unit=unit, utc=True) + pd.Timedelta(minutes=1)
    values: dict[str, Any] = {}
    for name in ("open", "high", "low", "close", "volume", "taker_buy_volume"):
        values[name] = pd.to_numeric(raw[name], errors="raise").to_numpy(copy=True)
    result = pd.DataFrame(values, index=index)
    if result.index.has_duplicates or not result.index.is_monotonic_increasing:
        raise RuntimeError("market-data timestamps are not strictly increasing and unique")
    if (result["volume"] < 0).any() or (result["taker_buy_volume"] < 0).any():
        raise RuntimeError("negative volume in source data")
    if (result["taker_buy_volume"] > result["volume"] + 1e-9).any():
        raise RuntimeError("taker-buy volume exceeds total volume")
    return result, manifest

