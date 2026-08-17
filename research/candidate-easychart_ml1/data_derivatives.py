"""Checksum-verified Binance USD-M derivatives metrics.

Binance Vision publishes five-minute USD-M metrics containing open interest and
positioning ratios.  Timestamps are observation times.  Consumers must join
backward/as-of so a decision never sees a later metric row.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import urllib.error

import numpy as np
import pandas as pd

from data import _download, sha256_file

BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
METRIC_COLUMNS = [
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
]
NUMERIC_COLUMNS = METRIC_COLUMNS[2:]


def _normalise_columns(frame: pd.DataFrame) -> pd.DataFrame:
    renamed = {str(column).strip().lower(): column for column in frame.columns}
    if set(METRIC_COLUMNS).issubset(renamed):
        return frame[[renamed[name] for name in METRIC_COLUMNS]].rename(
            columns={renamed[name]: name for name in METRIC_COLUMNS},
        )
    if frame.shape[1] == len(METRIC_COLUMNS):
        frame = frame.copy()
        frame.columns = METRIC_COLUMNS
        if str(frame.iloc[0]["create_time"]).strip().lower() in {
            "create_time",
            "create time",
        }:
            frame = frame.iloc[1:].copy()
        return frame
    raise RuntimeError(f"unexpected metrics schema: {list(frame.columns)}")


def load_metrics_day(symbol: str, day: date, cache: Path) -> pd.DataFrame:
    stamp = day.isoformat()
    name = f"{symbol}-metrics-{stamp}.zip"
    url = f"{BASE}/{symbol}/{name}"
    archive = cache / "metrics" / symbol / name
    checksum = archive.with_suffix(archive.suffix + ".CHECKSUM")
    try:
        _download(url, archive)
        _download(url + ".CHECKSUM", checksum)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return pd.DataFrame(columns=["metric_ts", *NUMERIC_COLUMNS])
        raise
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch {archive}: {actual} != {expected}")
    try:
        raw = pd.read_csv(archive, compression="zip")
        raw = _normalise_columns(raw)
    except Exception:
        raw = pd.read_csv(archive, compression="zip", header=None)
        raw = _normalise_columns(raw)
    raw = raw.loc[raw["symbol"].astype(str).str.upper().eq(symbol.upper())].copy()
    for column in NUMERIC_COLUMNS:
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw["metric_ts"] = pd.to_datetime(raw["create_time"], utc=True, errors="coerce")
    raw = raw.dropna(subset=["metric_ts", "sum_open_interest_value"])
    raw = raw[["metric_ts", *NUMERIC_COLUMNS]].sort_values("metric_ts")
    return raw.drop_duplicates("metric_ts", keep="last")


def load_metrics_range(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    day = start
    while day <= end:
        frame = load_metrics_day(symbol, day, cache)
        if not frame.empty:
            frames.append(frame)
        day += timedelta(days=1)
    if not frames:
        return pd.DataFrame(columns=["metric_ts", *NUMERIC_COLUMNS])
    result = pd.concat(frames, ignore_index=True).sort_values("metric_ts")
    return result.drop_duplicates("metric_ts", keep="last")


def _prior_robust_z(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    prior = series.shift(1)
    median = prior.rolling(window, min_periods=min_periods).median()
    absolute = (prior - median).abs()
    mad = absolute.rolling(window, min_periods=min_periods).median()
    scale = (1.4826 * mad).replace(0.0, np.nan)
    return (series - median) / scale


def enrich_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    if metrics.empty:
        return metrics.copy()
    frame = metrics.copy().set_index("metric_ts").sort_index()
    oi_value = frame["sum_open_interest_value"].astype(float).clip(lower=1e-12)
    oi_contracts = frame["sum_open_interest"].astype(float).clip(lower=1e-12)
    frame["oi_value_log"] = np.log(oi_value)
    frame["oi_contracts_log"] = np.log(oi_contracts)
    for bars, minutes in ((1, 5), (3, 15), (6, 30), (12, 60), (36, 180)):
        frame[f"oi_value_change_{minutes}"] = frame["oi_value_log"].diff(bars)
        frame[f"oi_contracts_change_{minutes}"] = frame["oi_contracts_log"].diff(bars)
    frame["oi_change_15_z"] = _prior_robust_z(
        frame["oi_value_change_15"],
        window=12 * 24 * 7,
        min_periods=12 * 24,
    )
    frame["oi_change_60_z"] = _prior_robust_z(
        frame["oi_value_change_60"],
        window=12 * 24 * 7,
        min_periods=12 * 24,
    )
    for source, target in (
        ("sum_taker_long_short_vol_ratio", "metric_taker_imbalance"),
        ("count_long_short_ratio", "global_account_imbalance"),
        ("count_toptrader_long_short_ratio", "top_account_imbalance"),
        ("sum_toptrader_long_short_ratio", "top_position_imbalance"),
    ):
        ratio = frame[source].astype(float).clip(lower=1e-6)
        frame[target] = (ratio - 1.0) / (ratio + 1.0)
        frame[f"{target}_z"] = _prior_robust_z(
            frame[target],
            window=12 * 24 * 7,
            min_periods=12 * 24,
        )
    return frame.replace([np.inf, -np.inf], np.nan)


def join_metrics_causally(frame: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    """Backward-asof join; metric age exposes gaps instead of silently hiding them."""
    result = frame.copy().sort_index()
    if metrics.empty:
        result["metric_age_minutes"] = np.nan
        return result
    right = enrich_metrics(metrics).reset_index().sort_values("metric_ts")
    left = result.reset_index().rename(columns={result.index.name or "index": "ts"})
    joined = pd.merge_asof(
        left.sort_values("ts"),
        right,
        left_on="ts",
        right_on="metric_ts",
        direction="backward",
        allow_exact_matches=True,
        tolerance=pd.Timedelta(minutes=20),
    )
    joined["metric_age_minutes"] = (
        joined["ts"] - joined["metric_ts"]
    ) / pd.Timedelta(minutes=1)
    return joined.set_index("ts").sort_index()
