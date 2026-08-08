#!/usr/bin/env python3
"""Build one causally warmed, calendar-trimmed feature chunk for Candidate 29.

Raw Binance archives are checksum verified by the inherited Candidate 05
contracts. Each chunk includes a short pre-period warmup, but only rows whose
completed-bar observation time falls inside the requested core interval are
published. The final account is not run here and is never reset by chunk.

Binance metrics archives occasionally overlap at a UTC day boundary and the two
archives can carry different values for the same five-minute timestamp.  File
order is not an admissible tie-break.  Candidate 29b gives each raw observation
to the archive whose filename date equals that observation's ``create_time``
date, then applies the inherited duplicate/conflict checks to the owned rows.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
import json
from pathlib import Path
import re
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE05 = HERE.parent / "candidate-05"
sys.path.insert(0, str(CANDIDATE05))

import positioning_contract as _positioning_contract
from timestamp_contract import install as install_timestamp_contract
from wrangler_contract import install as install_wrangler_contract
from positioning_contract import install as install_positioning_contract
from basis_contract import install as install_basis_contract
from book_depth_gap_contract import install as install_book_depth_gap_contract

install_timestamp_contract()
install_wrangler_contract()
install_positioning_contract()
install_basis_contract()
install_book_depth_gap_contract()

_METRICS_OWNER_RE = re.compile(r"-metrics-(\d{4}-\d{2}-\d{2})\.zip$")
_ORIGINAL_READ_METRICS = _positioning_contract._read_metrics


def _metrics_owner_day(path: Path) -> date:
    match = _METRICS_OWNER_RE.search(path.name)
    if match is None:
        raise RuntimeError(f"cannot infer metrics archive day from {path.name}")
    return date.fromisoformat(match.group(1))


def _keep_owned_metrics(frame: pd.DataFrame, owner_day: date) -> pd.DataFrame:
    """Keep only observations causally owned by the archive's UTC filename day."""
    if "metrics_observed_time" not in frame.columns:
        raise RuntimeError("metrics frame lacks metrics_observed_time")
    observed = pd.to_datetime(frame["metrics_observed_time"], utc=True, errors="raise")
    create_time = observed - pd.Timedelta(minutes=5)
    owned = frame.loc[create_time.dt.date == owner_day].copy()
    if owned.empty:
        raise RuntimeError(f"metrics archive produced no rows owned by {owner_day}")
    owned_observed = pd.to_datetime(
        owned["metrics_observed_time"],
        utc=True,
        errors="raise",
    )
    if owned_observed.duplicated().any() or not owned_observed.is_monotonic_increasing:
        raise RuntimeError(f"owned metrics rows are not unique and monotonic for {owner_day}")
    return owned


def _read_metrics_owned(path: Path) -> pd.DataFrame:
    frame = _ORIGINAL_READ_METRICS(path)
    return _keep_owned_metrics(frame, _metrics_owner_day(path))


# Patch only the raw daily-file ownership boundary.  The inherited positioning
# feature calculation, observation delay, as-of join and freshness checks stay
# unchanged.
_positioning_contract._read_metrics = _read_metrics_owned

from features import load_range
from features import sha256_file


class ChunkError(RuntimeError):
    """Raised when a chunk cannot be used in a continuous replay."""


def _expected_minutes(start: date, end: date) -> pd.DatetimeIndex:
    return pd.date_range(
        pd.Timestamp(start, tz="UTC"),
        pd.Timestamp(end + timedelta(days=1), tz="UTC") - pd.Timedelta(minutes=1),
        freq="1min",
    )


def _validate_exact_minutes(
    *,
    label: str,
    minute_values: pd.Series,
    start: date,
    end: date,
) -> None:
    actual = pd.DatetimeIndex(pd.to_datetime(minute_values, utc=True).dt.floor("min"))
    expected = _expected_minutes(start, end)
    if actual.has_duplicates:
        duplicates = actual[actual.duplicated()].unique()[:5]
        raise ChunkError(f"{label} has duplicate minutes: {list(map(str, duplicates))}")
    if len(actual) != len(expected) or not actual.equals(expected):
        missing = expected.difference(actual)[:10]
        extra = actual.difference(expected)[:10]
        raise ChunkError(
            f"{label} is not a complete minute grid: "
            f"actual={len(actual)} expected={len(expected)} "
            f"missing={list(map(str, missing))} extra={list(map(str, extra))}",
        )


def build_chunk(
    *,
    symbol: str,
    core_start: date,
    core_end: date,
    warmup_days: int,
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    if core_end < core_start:
        raise ValueError("core end precedes core start")
    if warmup_days < 3:
        raise ValueError("at least three warmup days are required")
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    build_start = core_start - timedelta(days=warmup_days)
    build_end = core_end
    klines, source_feature_path, _, evidence = load_range(
        symbol=symbol,
        start=build_start,
        end=build_end,
        cache=cache,
        output=output / "source",
    )

    core_open = pd.Timestamp(core_start, tz="UTC")
    core_close = pd.Timestamp(core_end + timedelta(days=1), tz="UTC")

    klines = klines.copy()
    klines["close_time_dt"] = pd.to_datetime(
        klines["close_time_dt"],
        utc=True,
        errors="raise",
    )
    klines["open_time_dt"] = pd.to_datetime(
        klines["open_time_dt"],
        utc=True,
        errors="raise",
    )
    klines = klines[
        (klines["close_time_dt"] >= core_open)
        & (klines["close_time_dt"] < core_close)
    ].copy()
    klines = klines.sort_values("close_time_dt").reset_index(drop=True)

    features = pd.read_csv(source_feature_path, compression="infer")
    features["observed_time_ns"] = pd.to_numeric(
        features["observed_time_ns"],
        errors="raise",
    ).astype("int64")
    observed = pd.to_datetime(features["observed_time_ns"], unit="ns", utc=True)
    features = features[(observed >= core_open) & (observed < core_close)].copy()
    features = features.sort_values("observed_time_ns").reset_index(drop=True)

    _validate_exact_minutes(
        label="klines",
        minute_values=klines["close_time_dt"],
        start=core_start,
        end=core_end,
    )
    _validate_exact_minutes(
        label="features",
        minute_values=pd.Series(
            pd.to_datetime(features["observed_time_ns"], unit="ns", utc=True),
        ),
        start=core_start,
        end=core_end,
    )

    kline_ns = pd.Series(
        (pd.Timestamp(value).value for value in klines["close_time_dt"]),
        dtype="int64",
    )
    feature_ns = features["observed_time_ns"].reset_index(drop=True)
    if not kline_ns.equals(feature_ns):
        mismatch = (kline_ns != feature_ns).to_numpy().nonzero()[0][:5]
        raise ChunkError(f"kline/feature observation mismatch at rows {mismatch.tolist()}")

    kline_path = output / "klines.csv.gz"
    feature_path = output / "features.csv.gz"
    klines.to_csv(kline_path, index=False, compression="gzip")
    features.to_csv(feature_path, index=False, compression="gzip")

    endpoints = Counter(item.endpoint for item in evidence)
    manifest = {
        "schema_version": 2,
        "candidate": "candidate-29b-continuous-replay",
        "symbol": symbol,
        "core_start": core_start.isoformat(),
        "core_end": core_end.isoformat(),
        "build_start": build_start.isoformat(),
        "build_end": build_end.isoformat(),
        "warmup_days": warmup_days,
        "metrics_boundary_policy": "create_time_owned_by_filename_utc_day",
        "calendar_days": (core_end - core_start).days + 1,
        "rows": len(klines),
        "first_observed_time_ns": int(feature_ns.iloc[0]),
        "last_observed_time_ns": int(feature_ns.iloc[-1]),
        "feature_ready_rows": int(
            features["feature_ready"].astype(str).str.lower().isin({"true", "1", "yes"}).sum()
        ),
        "evidence_files": len(evidence),
        "evidence_endpoints": dict(sorted(endpoints.items())),
        "source_evidence": [asdict(item) for item in evidence],
        "files": {
            "klines.csv.gz": {
                "size_bytes": kline_path.stat().st_size,
                "sha256": sha256_file(kline_path),
            },
            "features.csv.gz": {
                "size_bytes": feature_path.stat().st_size,
                "sha256": sha256_file(feature_path),
            },
        },
    }
    (output / "chunk_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--core-start", required=True)
    parser.add_argument("--core-end", required=True)
    parser.add_argument("--warmup-days", type=int, default=3)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_chunk(
        symbol=args.symbol,
        core_start=date.fromisoformat(args.core_start),
        core_end=date.fromisoformat(args.core_end),
        warmup_days=args.warmup_days,
        cache=args.cache,
        output=args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
