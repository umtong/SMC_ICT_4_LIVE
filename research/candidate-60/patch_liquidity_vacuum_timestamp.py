#!/usr/bin/env python3
"""Apply the sole engineering repair to the frozen liquidity-vacuum source.

Pandas 3 may preserve timezone-aware datetimes with millisecond storage.  The
external wrapper used ``Series.astype('int64')`` and called the result
nanoseconds, collapsing all rows to the same minute when divided by
60_000_000_000.  This patch does not alter any market-state, entry, stop,
target, cost, or cooldown rule.  It reconstructs the observation and minute
clocks from the exact returned kline rows using an explicit datetime64[ns]
conversion and proves that the original integer clock differs only by a fixed
unit scale.
"""
from __future__ import annotations

import argparse
from pathlib import Path


OLD = '''    base = pd.read_csv(feature_path, compression="infer")
    observed = pd.to_numeric(base["observed_time_ns"], errors="raise").astype("int64")
    base["minute_start_ns"] = observed // NS_PER_MINUTE * NS_PER_MINUTE
    merged = base.merge(
'''

NEW = '''    base = pd.read_csv(feature_path, compression="infer")
    if len(base) != len(klines):
        raise RuntimeError(
            f"perpetual feature/kline row mismatch: {len(base)} != {len(klines)}"
        )
    original_observed = pd.to_numeric(
        base["observed_time_ns"], errors="raise"
    ).astype("int64").to_numpy()
    close_ns = _datetime_ns(klines["close_time_dt"])
    open_ns = _datetime_ns(klines["open_time_dt"])
    scale_matches = [
        factor
        for factor in (1, 1_000, 1_000_000)
        if np.array_equal(original_observed * factor, close_ns)
    ]
    if len(scale_matches) != 1:
        raise RuntimeError(
            "original perpetual observation clock is not a single-unit view of kline closes"
        )
    base["observed_time_ns"] = close_ns
    base["minute_start_ns"] = open_ns
    if base["minute_start_ns"].duplicated().any():
        raise RuntimeError("normalized perpetual minute clock is not unique")
    merged = base.merge(
'''


def patch(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(OLD)
    if count != 1:
        raise RuntimeError(f"expected one frozen timestamp block, found {count}")
    updated = source.replace(OLD, NEW)
    path.write_text(updated, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch(args.path.resolve())


if __name__ == "__main__":
    main()
