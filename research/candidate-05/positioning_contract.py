"""Causal Binance USD-M positioning observations for Candidate 05.

Official five-minute metrics archives are checksum-verified and delayed by one
full metrics interval before they become observable to the strategy.  This
module only enriches the observational feature file; it does not match orders,
compute PnL, or maintain account state.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from functools import wraps
import json
from pathlib import Path
import urllib.request
from typing import Any, Callable

import pandas as pd

import features as _features

_BASE_LOAD_RANGE: Callable[..., Any] | None = None
_METRICS_COLUMNS = (
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)


def _as_utc_nanoseconds(values: pd.Series) -> pd.Series:
    """Normalize a timestamp series to timezone-aware nanosecond resolution."""
    parsed = pd.to_datetime(values, utc=True, errors="raise")
    return parsed.astype("datetime64[ns, UTC]")


def _download_metrics(symbol: str, day: date, cache: Path):
    stamp = day.isoformat()
    filename = f"{symbol}-metrics-{stamp}.zip"
    url = f"{_features.BASE}/metrics/{symbol}/{filename}"
    directory = cache / "metrics"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _features.sha256_file(archive)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    evidence = _features.RawEvidence(
        endpoint="metrics",
        day=stamp,
        archive=str(archive),
        checksum=str(checksum),
        size_bytes=archive.stat().st_size,
        sha256=actual,
    )
    return archive, checksum, evidence


def _read_metrics(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip")
    required = {"create_time", "symbol", *_METRICS_COLUMNS}
    if not required.issubset(raw.columns):
        raise RuntimeError(f"unexpected metrics schema in {path}: {list(raw.columns)}")
    raw["create_time"] = _as_utc_nanoseconds(raw["create_time"])
    for column in _METRICS_COLUMNS:
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    raw = raw.sort_values("create_time")
    if raw["create_time"].duplicated().any():
        raise RuntimeError(f"duplicate metrics timestamps in {path}")
    raw["metrics_observed_time"] = _as_utc_nanoseconds(
        raw["create_time"] + pd.Timedelta(minutes=5),
    )
    return raw[["metrics_observed_time", *_METRICS_COLUMNS]].copy()


def _positioning_features(metrics: pd.DataFrame) -> pd.DataFrame:
    frame = metrics.sort_values("metrics_observed_time").copy()
    frame["metrics_observed_time"] = _as_utc_nanoseconds(
        frame["metrics_observed_time"],
    )
    if frame["metrics_observed_time"].duplicated().any():
        raise RuntimeError("duplicate combined metrics observation timestamps")
    frame["oi_change_5m"] = frame["sum_open_interest"].pct_change(1, fill_method=None)
    frame["oi_change_15m"] = frame["sum_open_interest"].pct_change(3, fill_method=None)
    frame["oi_change_30m"] = frame["sum_open_interest"].pct_change(6, fill_method=None)
    frame["oi_value_change_15m"] = frame["sum_open_interest_value"].pct_change(3, fill_method=None)
    frame["top_position_ratio_change_15m"] = frame[
        "sum_toptrader_long_short_ratio"
    ].pct_change(3, fill_method=None)
    frame["account_ratio_change_15m"] = frame["count_long_short_ratio"].pct_change(
        3,
        fill_method=None,
    )
    frame["metrics_observed_time_ns"] = frame["metrics_observed_time"].astype("int64")
    return frame


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
):
    if _BASE_LOAD_RANGE is None:
        raise RuntimeError("positioning contract was not installed")
    klines, feature_path, manifest_files, evidence = _BASE_LOAD_RANGE(
        symbol=symbol,
        start=start,
        end=end,
        cache=cache,
        output=output,
    )

    metric_frames: list[pd.DataFrame] = []
    day = start
    while day <= end:
        archive, checksum, item = _download_metrics(symbol, day, cache)
        metric_frames.append(_read_metrics(archive))
        manifest_files.extend([archive, checksum])
        evidence.append(item)
        day += timedelta(days=1)

    metrics = _positioning_features(pd.concat(metric_frames, ignore_index=True))
    features = pd.read_csv(feature_path, compression="infer")
    features["feature_observed_time"] = _as_utc_nanoseconds(
        pd.to_datetime(
            pd.to_numeric(features["observed_time_ns"], errors="raise").astype("int64"),
            unit="ns",
            utc=True,
        ),
    )
    if features["feature_observed_time"].dtype != metrics["metrics_observed_time"].dtype:
        raise RuntimeError(
            "feature and metrics observation timestamps have different resolutions: "
            f"{features['feature_observed_time'].dtype} != {metrics['metrics_observed_time'].dtype}",
        )
    joined = pd.merge_asof(
        features.sort_values("feature_observed_time"),
        metrics.sort_values("metrics_observed_time"),
        left_on="feature_observed_time",
        right_on="metrics_observed_time",
        direction="backward",
        allow_exact_matches=True,
    )
    joined["metrics_age_seconds"] = (
        joined["feature_observed_time"] - joined["metrics_observed_time"]
    ).dt.total_seconds()
    if joined["metrics_age_seconds"].dropna().lt(0.0).any():
        raise RuntimeError("future positioning snapshot reached feature rows")
    joined["metrics_ready"] = (
        joined["oi_change_15m"].notna()
        & joined["metrics_age_seconds"].ge(0.0)
        & joined["metrics_age_seconds"].le(600.0)
    )
    joined = joined.drop(columns=["feature_observed_time", "metrics_observed_time"])
    joined.to_csv(feature_path, index=False, compression="gzip")
    (output / "raw_evidence.json").write_text(
        json.dumps([asdict(item) for item in evidence], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return klines, feature_path, manifest_files, evidence


def install() -> None:
    """Wrap the currently installed feature loader, preserving prior contracts."""
    global _BASE_LOAD_RANGE
    current = _features.load_range
    if getattr(current, "_candidate05_positioning_contract", False):
        return
    _BASE_LOAD_RANGE = current
    wrapped = wraps(current)(load_range)
    setattr(wrapped, "_candidate05_positioning_contract", True)
    _features.load_range = wrapped
