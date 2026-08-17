"""Checksum-verified Binance Vision derivative context for ML1 research.

The loader is intentionally narrow: USD-M futures 5-minute metrics and premium
index klines. It uses only records whose timestamp is already available at a
decision instant and tolerates schema revisions by normalizing known aliases.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import hashlib
import io
import urllib.request
import zipfile

import numpy as np
import pandas as pd

VISION = "https://data.binance.vision/data/futures/um/daily"


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    request = urllib.request.Request(url, headers={"User-Agent": "smc-ict-ml1/2.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(payload)
    tmp.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_archive(url: str, path: Path) -> Path:
    checksum = path.with_suffix(path.suffix + ".CHECKSUM")
    _download(url, path)
    _download(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256(path)
    if actual != expected:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {path}: {actual} != {expected}")
    return path


def _read_zip_csv(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected members in {path}: {names}")
        return pd.read_csv(io.BytesIO(archive.read(names[0])))


def _timestamp(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().mean() > 0.95:
        finite = numeric.dropna().abs()
        median = float(finite.median()) if not finite.empty else 0.0
        unit = "us" if median > 1e14 else "ms" if median > 1e11 else "s"
        return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
    return pd.to_datetime(values, utc=True, errors="coerce")


def _iter_days(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


_METRIC_ALIASES = {
    "sum_open_interest": "open_interest",
    "sum_open_interest_value": "open_interest_value",
    "count_toptrader_long_short_ratio": "top_account_ratio",
    "sum_toptrader_long_short_ratio": "top_position_ratio",
    "count_long_short_ratio": "global_account_ratio",
    "sum_taker_long_short_vol_ratio": "taker_long_short_ratio",
}


def load_metrics_day(symbol: str, day: date, cache: Path) -> pd.DataFrame:
    stamp = day.isoformat()
    name = f"{symbol}-metrics-{stamp}.zip"
    url = f"{VISION}/metrics/{symbol}/{name}"
    path = _verified_archive(url, cache / "metrics" / symbol / name)
    frame = _read_zip_csv(path)
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    time_column = next(
        (column for column in ("create_time", "timestamp", "time") if column in frame.columns),
        None,
    )
    if time_column is None:
        raise RuntimeError(f"missing metrics timestamp in {path}: {list(frame.columns)}")
    output = pd.DataFrame({"time": _timestamp(frame[time_column])})
    for source, target in _METRIC_ALIASES.items():
        output[target] = pd.to_numeric(frame[source], errors="coerce") if source in frame else np.nan
    return output.dropna(subset=["time"]).sort_values("time").drop_duplicates("time")


def load_metrics_range(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for day in _iter_days(start, end):
        try:
            frames.append(load_metrics_day(symbol, day, cache))
        except Exception as exc:
            if "404" not in str(exc) and "Not Found" not in str(exc):
                raise
    if not frames:
        return pd.DataFrame(columns=["time", *_METRIC_ALIASES.values()])
    return pd.concat(frames, ignore_index=True).sort_values("time").drop_duplicates("time")


_KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def load_premium_day(symbol: str, day: date, cache: Path) -> pd.DataFrame:
    stamp = day.isoformat()
    name = f"{symbol}-1m-{stamp}.zip"
    url = f"{VISION}/premiumIndexKlines/{symbol}/1m/{name}"
    path = _verified_archive(url, cache / "premium" / symbol / name)
    raw = pd.read_csv(path, compression="zip", header=None)
    if raw.shape[1] == len(_KLINE_COLUMNS):
        raw.columns = _KLINE_COLUMNS
        if not str(raw.iloc[0]["open_time"]).lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    else:
        raw = pd.read_csv(path, compression="zip")
        raw.columns = [str(column).strip().lower() for column in raw.columns]
    output = pd.DataFrame(
        {
            "time": _timestamp(raw["open_time"]),
            "premium_open": pd.to_numeric(raw["open"], errors="coerce"),
            "premium_high": pd.to_numeric(raw["high"], errors="coerce"),
            "premium_low": pd.to_numeric(raw["low"], errors="coerce"),
            "premium_close": pd.to_numeric(raw["close"], errors="coerce"),
        }
    )
    output["time"] = output["time"] + pd.Timedelta(minutes=1)
    return output.dropna(subset=["time"]).sort_values("time").drop_duplicates("time")


def load_premium_range(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for day in _iter_days(start, end):
        try:
            frames.append(load_premium_day(symbol, day, cache))
        except Exception as exc:
            if "404" not in str(exc) and "Not Found" not in str(exc):
                raise
    if not frames:
        return pd.DataFrame(columns=["time", "premium_open", "premium_high", "premium_low", "premium_close"])
    return pd.concat(frames, ignore_index=True).sort_values("time").drop_duplicates("time")


def attach_derivatives_context(
    bars: pd.DataFrame,
    metrics: pd.DataFrame,
    premium: pd.DataFrame,
) -> pd.DataFrame:
    """Backward-as-of attach; no future metrics or premium bars can leak."""
    output = bars.sort_values("open_time_dt").copy()
    output = output.assign(decision_time=output["open_time_dt"] + pd.Timedelta(minutes=1))
    if not metrics.empty:
        output = pd.merge_asof(
            output.sort_values("decision_time"),
            metrics.sort_values("time"),
            left_on="decision_time",
            right_on="time",
            direction="backward",
            tolerance=pd.Timedelta(minutes=10),
        ).drop(columns=["time"], errors="ignore")
    else:
        for column in _METRIC_ALIASES.values():
            output[column] = np.nan
    if not premium.empty:
        output = pd.merge_asof(
            output.sort_values("decision_time"),
            premium.sort_values("time"),
            left_on="decision_time",
            right_on="time",
            direction="backward",
            tolerance=pd.Timedelta(minutes=2),
        ).drop(columns=["time"], errors="ignore")
    else:
        for column in ("premium_open", "premium_high", "premium_low", "premium_close"):
            output[column] = np.nan
    return output.drop(columns=["decision_time"]).sort_values("open_time_dt").reset_index(drop=True)
