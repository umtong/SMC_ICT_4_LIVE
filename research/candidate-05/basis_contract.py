"""Causal Binance USD-M premium-index observations for Candidate 05.

The premium-index kline for a minute becomes usable only at its close timestamp.
This wrapper enriches the existing checksum-verified feature file; it never
matches orders, creates fills, maintains positions, or calculates PnL.
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


def _as_utc_nanoseconds(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, utc=True, errors="raise")
    return parsed.astype("datetime64[ns, UTC]")


def _download_premium(symbol: str, day: date, cache: Path):
    stamp = day.isoformat()
    filename = f"{symbol}-1m-{stamp}.zip"
    url = f"{_features.BASE}/premiumIndexKlines/{symbol}/1m/{filename}"
    directory = cache / "premiumIndexKlines"
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
        endpoint="premiumIndexKlines",
        day=stamp,
        archive=str(archive),
        checksum=str(checksum),
        size_bytes=archive.stat().st_size,
        sha256=actual,
    )
    return archive, checksum, evidence


def _premium_features(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"close_time_dt", "close"}
    if not required.issubset(frame.columns):
        raise RuntimeError(f"premium-index frame missing {sorted(required)}")
    result = frame[["close_time_dt", "close"]].copy()
    result["premium_observed_time"] = _as_utc_nanoseconds(result["close_time_dt"])
    result["premium_index"] = pd.to_numeric(result["close"], errors="raise")
    result = result.sort_values("premium_observed_time")
    if result["premium_observed_time"].duplicated().any():
        raise RuntimeError("duplicate premium-index observation timestamps")
    # Binance premiumIndexKlines already contain a dimensionless premium ratio.
    # The causal state uses the absolute change in that ratio, not a percentage
    # change of a value which can cross or approach zero.
    result["premium_change_1m"] = result["premium_index"].diff(1)
    result["premium_change_5m"] = result["premium_index"].diff(5)
    result["premium_change_15m"] = result["premium_index"].diff(15)
    return result[
        [
            "premium_observed_time",
            "premium_index",
            "premium_change_1m",
            "premium_change_5m",
            "premium_change_15m",
        ]
    ]


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
):
    if _BASE_LOAD_RANGE is None:
        raise RuntimeError("basis contract was not installed")
    klines, feature_path, manifest_files, evidence = _BASE_LOAD_RANGE(
        symbol=symbol,
        start=start,
        end=end,
        cache=cache,
        output=output,
    )

    premium_frames: list[pd.DataFrame] = []
    day = start
    while day <= end:
        archive, checksum, item = _download_premium(symbol, day, cache)
        premium_frames.append(_features.read_kline(archive))
        manifest_files.extend([archive, checksum])
        evidence.append(item)
        day += timedelta(days=1)

    premium = _premium_features(pd.concat(premium_frames, ignore_index=True))
    features = pd.read_csv(feature_path, compression="infer")
    features["feature_observed_time"] = _as_utc_nanoseconds(
        pd.to_datetime(
            pd.to_numeric(features["observed_time_ns"], errors="raise").astype("int64"),
            unit="ns",
            utc=True,
        ),
    )
    if features["feature_observed_time"].dtype != premium["premium_observed_time"].dtype:
        raise RuntimeError(
            "feature and premium observation timestamps have different resolutions: "
            f"{features['feature_observed_time'].dtype} != "
            f"{premium['premium_observed_time'].dtype}",
        )
    joined = pd.merge_asof(
        features.sort_values("feature_observed_time"),
        premium.sort_values("premium_observed_time"),
        left_on="feature_observed_time",
        right_on="premium_observed_time",
        direction="backward",
        allow_exact_matches=True,
    )
    joined["premium_age_seconds"] = (
        joined["feature_observed_time"] - joined["premium_observed_time"]
    ).dt.total_seconds()
    if joined["premium_age_seconds"].dropna().lt(0.0).any():
        raise RuntimeError("future premium-index observation reached feature rows")
    joined["basis_ready"] = (
        joined["premium_change_5m"].notna()
        & joined["premium_age_seconds"].ge(0.0)
        & joined["premium_age_seconds"].le(65.0)
    )
    joined = joined.drop(columns=["feature_observed_time", "premium_observed_time"])
    joined.to_csv(feature_path, index=False, compression="gzip")
    (output / "raw_evidence.json").write_text(
        json.dumps([asdict(item) for item in evidence], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return klines, feature_path, manifest_files, evidence


def install() -> None:
    """Wrap the current feature loader after timestamp and positioning contracts."""
    global _BASE_LOAD_RANGE
    current = _features.load_range
    if getattr(current, "_candidate05_basis_contract", False):
        return
    _BASE_LOAD_RANGE = current
    wrapped = wraps(current)(load_range)
    setattr(wrapped, "_candidate05_basis_contract", True)
    _features.load_range = wrapped
