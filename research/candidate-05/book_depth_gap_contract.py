"""Explicit no-trade handling for missing Binance bookDepth archive days.

A missing daily bookDepth object is not imputed, interpolated or replaced with
future information.  The contract records a checksum-verifiable local gap
sentinel, emits zero-depth placeholder rows only to prevent stale forward fill,
and marks the entire missing day plus the first five minutes after depth resumes
as feature-unready.  NautilusTrader still receives the complete price path and
continues to own orders, fills, positions, fees, margin and NAV.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from functools import wraps
from hashlib import sha256
import json
from pathlib import Path
import urllib.error
from typing import Any, Callable

import numpy as np
import pandas as pd

import features as _features


_BASE_DOWNLOAD_CHECKED: Callable[..., Any] | None = None
_BASE_AGGREGATE_BOOK_DEPTH: Callable[..., pd.DataFrame] | None = None
_BASE_BUILD_FEATURES: Callable[..., pd.DataFrame] | None = None
_GAP_SUFFIX = ".missing-book-depth.json"
_DEPTH_COLUMNS = (
    "depth_imbalance_1",
    "depth_imbalance_2",
    "bid_depth_change_1_1m",
    "ask_depth_change_1_1m",
    "bid_depth_change_1_5m",
    "ask_depth_change_1_5m",
    "bid_depth_change_2_1m",
    "ask_depth_change_2_1m",
    "bid_depth_change_2_5m",
    "ask_depth_change_2_5m",
)


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _gap_paths(cache: Path, symbol: str, day: date) -> tuple[Path, Path]:
    directory = Path(cache) / "bookDepth"
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{symbol}-bookDepth-{day.isoformat()}"
    sentinel = directory / f"{stem}{_GAP_SUFFIX}"
    checksum = directory / f"{sentinel.name}.CHECKSUM"
    return sentinel, checksum


def _write_gap_sentinel(
    *,
    symbol: str,
    day: date,
    cache: Path,
    source_url: str,
) -> tuple[Path, Path, _features.RawEvidence]:
    sentinel, checksum = _gap_paths(cache, symbol, day)
    payload = {
        "schema": "candidate-05-book-depth-gap-v1",
        "endpoint": "bookDepth",
        "symbol": symbol,
        "day": day.isoformat(),
        "http_status": 404,
        "source_url": source_url,
        "policy": "NO_IMPUTATION_FEATURE_UNREADY_NO_NEW_ENTRY",
    }
    raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    digest = _sha256_bytes(raw)
    sentinel.write_bytes(raw)
    checksum.write_text(f"{digest}  {sentinel.name}\n", encoding="utf-8")
    evidence = _features.RawEvidence(
        endpoint="bookDepth_missing_404",
        day=day.isoformat(),
        archive=str(sentinel),
        checksum=str(checksum),
        size_bytes=len(raw),
        sha256=digest,
    )
    return sentinel, checksum, evidence


def download_checked(
    endpoint: str,
    symbol: str,
    day: date,
    cache: Path,
):
    """Delegate normally; convert only an authoritative bookDepth 404 to a gap."""
    if _BASE_DOWNLOAD_CHECKED is None:
        raise RuntimeError("book-depth gap contract was not installed")
    try:
        return _BASE_DOWNLOAD_CHECKED(endpoint, symbol, day, cache)
    except urllib.error.HTTPError as exc:
        if endpoint != "bookDepth" or int(exc.code) != 404:
            raise
        source_url, _ = _features._archive_spec(endpoint, symbol, day)
        return _write_gap_sentinel(
            symbol=symbol,
            day=day,
            cache=cache,
            source_url=source_url,
        )


def _gap_depth_frame(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "candidate-05-book-depth-gap-v1":
        raise RuntimeError(f"invalid book-depth gap sentinel: {path}")
    day = date.fromisoformat(str(payload["day"]))
    start = pd.Timestamp(day, tz="UTC")
    index = pd.date_range(start=start, periods=1_440, freq="min", name="minute")
    frame = pd.DataFrame(index=index)
    frame["depth_snapshot_time"] = index
    # Literal zeros are not market observations. They are a blocking sentinel:
    # imbalance becomes NaN (0/0), so no depth-dependent decision can pass.
    frame["bid_depth_1"] = 0.0
    frame["ask_depth_1"] = 0.0
    frame["bid_depth_2"] = 0.0
    frame["ask_depth_2"] = 0.0
    frame["depth_data_gap"] = True
    return frame


def aggregate_book_depth(path: Path) -> pd.DataFrame:
    if _BASE_AGGREGATE_BOOK_DEPTH is None:
        raise RuntimeError("book-depth gap contract was not installed")
    path = Path(path)
    if path.name.endswith(_GAP_SUFFIX):
        return _gap_depth_frame(path)
    frame = _BASE_AGGREGATE_BOOK_DEPTH(path).copy()
    frame["depth_data_gap"] = False
    return frame


def build_features(
    klines: pd.DataFrame,
    agg: pd.DataFrame,
    depth: pd.DataFrame,
) -> pd.DataFrame:
    """Preserve normal features while making missing-depth minutes untradable."""
    if _BASE_BUILD_FEATURES is None:
        raise RuntimeError("book-depth gap contract was not installed")
    gap = depth.get("depth_data_gap")
    blocked_index = pd.DatetimeIndex([], tz="UTC")
    if gap is not None:
        gap_mask = pd.Series(gap, index=depth.index).fillna(False).astype(bool)
        gap_index = pd.DatetimeIndex(depth.index[gap_mask])
        if len(gap_index):
            blocked = set(gap_index)
            for timestamp in gap_index:
                # Five minutes are also blocked after the gap so 1m/5m depth
                # changes cannot compare a real snapshot against the sentinel.
                for offset in range(1, 6):
                    blocked.add(timestamp + pd.Timedelta(minutes=offset))
            blocked_index = pd.DatetimeIndex(sorted(blocked))

    result = _BASE_BUILD_FEATURES(
        klines,
        agg,
        depth.drop(columns=["depth_data_gap"], errors="ignore"),
    )
    result["depth_data_gap"] = result.index.isin(blocked_index)
    if len(blocked_index):
        mask = result["depth_data_gap"]
        result.loc[mask, "feature_ready"] = False
        for column in _DEPTH_COLUMNS:
            if column in result.columns:
                result.loc[mask, column] = np.nan
    return result


def install() -> None:
    """Install the observational gap contract exactly once per process."""
    global _BASE_DOWNLOAD_CHECKED
    global _BASE_AGGREGATE_BOOK_DEPTH
    global _BASE_BUILD_FEATURES

    if getattr(_features.download_checked, "_candidate05_depth_gap_contract", False):
        return
    _BASE_DOWNLOAD_CHECKED = _features.download_checked
    _BASE_AGGREGATE_BOOK_DEPTH = _features.aggregate_book_depth
    _BASE_BUILD_FEATURES = _features.build_features

    wrapped_download = wraps(_features.download_checked)(download_checked)
    wrapped_aggregate = wraps(_features.aggregate_book_depth)(aggregate_book_depth)
    wrapped_build = wraps(_features.build_features)(build_features)
    setattr(wrapped_download, "_candidate05_depth_gap_contract", True)
    setattr(wrapped_aggregate, "_candidate05_depth_gap_contract", True)
    setattr(wrapped_build, "_candidate05_depth_gap_contract", True)
    _features.download_checked = wrapped_download
    _features.aggregate_book_depth = wrapped_aggregate
    _features.build_features = wrapped_build


__all__ = [
    "aggregate_book_depth",
    "build_features",
    "download_checked",
    "install",
]
