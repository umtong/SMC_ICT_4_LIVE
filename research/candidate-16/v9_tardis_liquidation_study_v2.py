#!/usr/bin/env python3
"""Timestamp-unit compatibility repair for the frozen v9 Tardis study.

Binance Vision kline timestamps are materialized by pandas as UTC milliseconds,
while Tardis microsecond timestamps materialize as UTC microseconds.  Pandas
``merge_asof`` requires the exact same dtype even though both represent the same
completed-minute clock.  This launcher changes no event, state, threshold,
outcome, cost, sample, or promotion rule.  It normalizes every joined minute to
UTC nanoseconds and invokes the frozen study unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import v9_tardis_liquidation_study as base


_ORIGINAL_READ_KLINE = base.read_kline
_ORIGINAL_READ_LIQUIDATIONS = base.read_tardis_liquidations
_ORIGINAL_READ_DERIVATIVE = base.read_tardis_derivative


def normalize_completed_minute(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "minute" not in result:
        raise base.StudyError("completed-minute frame has no minute column")
    result["minute"] = pd.to_datetime(
        result["minute"],
        utc=True,
        errors="raise",
    ).astype("datetime64[ns, UTC]")
    return result


def read_kline(*args, **kwargs):
    return normalize_completed_minute(_ORIGINAL_READ_KLINE(*args, **kwargs))


def read_liquidations(*args, **kwargs):
    return normalize_completed_minute(_ORIGINAL_READ_LIQUIDATIONS(*args, **kwargs))


def read_derivative(*args, **kwargs):
    return normalize_completed_minute(_ORIGINAL_READ_DERIVATIVE(*args, **kwargs))


base.read_kline = read_kline
base.read_tardis_liquidations = read_liquidations
base.read_tardis_derivative = read_derivative


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = base.run(args.cache.resolve(), args.output.resolve())
    result["timestamp_compatibility"] = {
        "canonical_dtype": "datetime64[ns, UTC]",
        "economic_logic_changed": False,
        "reason": "pandas merge_asof requires identical datetime resolution",
    }
    base.write_json(args.output / "study.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
