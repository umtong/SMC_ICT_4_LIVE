#!/usr/bin/env python3
"""Build Candidate 30 months without inventing missing premium observations.

The official premium-index archive has a few genuine minute gaps.  Price bars
remain the authoritative continuous event clock.  A missing premium row is left
missing by the inherited one-to-one left join and therefore produces
``basis_ready=False``; no forward fill, interpolation, file-order choice or
future observation is permitted.  Price and metric contracts remain strict.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

import pandas as pd

import build_month as _base
import build_month_v2  # noqa: F401  # installs explicit-nanosecond as-of join

_ORIGINAL_EXACT_MINUTE_GRID = _base._exact_minute_grid


def _premium_subset_grid(
    values: pd.Series,
    start: date,
    end: date,
) -> int:
    actual = pd.DatetimeIndex(pd.to_datetime(values, utc=True).dt.floor("min"))
    expected = pd.date_range(
        pd.Timestamp(start, tz="UTC"),
        pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(minutes=1),
        freq="1min",
    )
    if actual.has_duplicates:
        duplicate = actual[actual.duplicated()].unique()[:10]
        raise RuntimeError(f"premium has duplicate minute rows: {list(map(str, duplicate))}")
    if not actual.is_monotonic_increasing:
        raise RuntimeError("premium observations are not monotonic")
    extra = actual.difference(expected)
    if len(extra):
        raise RuntimeError(f"premium has observations outside core grid: {list(map(str, extra[:10]))}")
    return int(len(expected.difference(actual)))


def _exact_minute_grid_allow_premium_gaps(
    values: pd.Series,
    start: date,
    end: date,
    label: str,
) -> None:
    if label == "premium":
        _premium_subset_grid(values, start, end)
        return
    _ORIGINAL_EXACT_MINUTE_GRID(values, start, end, label)


_base._exact_minute_grid = _exact_minute_grid_allow_premium_gaps


def run_build(
    *,
    symbol: str,
    core_start: date,
    core_end: date,
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    result = _base.build(
        symbol=symbol,
        core_start=core_start,
        core_end=core_end,
        cache=cache,
        output=output,
    )
    data = pd.read_csv(output / "minute_state.csv.gz", compression="infer")
    ready = data["basis_ready"]
    if ready.dtype == bool:
        ready_count = int(ready.sum())
    else:
        ready_count = int(
            ready.astype(str).str.strip().str.lower().isin({"true", "1", "yes"}).sum()
        )
    missing = int(len(data) - ready_count)
    result["schema_version"] = 2
    result["premium_gap_policy"] = "missing_source_minute_is_basis_ready_false_no_fill"
    result["premium_missing_rows"] = missing
    result["basis_ready_rows"] = ready_count
    (output / "month_manifest.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--core-start", required=True)
    parser.add_argument("--core-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_build(
        symbol=args.symbol,
        core_start=date.fromisoformat(args.core_start),
        core_end=date.fromisoformat(args.core_end),
        cache=args.cache,
        output=args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
