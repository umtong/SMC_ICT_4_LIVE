#!/usr/bin/env python3
"""Run V18 with mixed millisecond/microsecond Binance spot timestamps.

Binance spot archives changed timestamp precision during the frozen V18 data
window.  This adapter normalizes each row independently to milliseconds.  It
changes no event, state, confirmation, route, cost, horizon or evaluation rule.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

import diagnose_v16_index_basis as common
import diagnose_v18_spot_perp as v18


def load_market_series(
    paths: list[Path],
    start: date,
    end: date,
) -> pd.DataFrame:
    if not paths:
        raise RuntimeError("no market archives")
    raw = pd.concat([common.read_zip(path) for path in paths], ignore_index=True)
    raw = raw.drop_duplicates("open_time", keep="last").sort_values("open_time")
    timestamps = pd.to_numeric(raw["open_time"], errors="raise").astype("int64")

    # Monthly futures archives use milliseconds.  Spot archives transition to
    # microseconds inside this evaluation window, so a single unit inferred
    # from the first row would discard all later observations.
    normalized_ms = timestamps.copy()
    microseconds = normalized_ms >= 1_000_000_000_000_000
    milliseconds = (
        (normalized_ms >= 1_000_000_000_000)
        & (normalized_ms < 10_000_000_000_000)
    )
    if not bool((microseconds | milliseconds).all()):
        bad = normalized_ms[~(microseconds | milliseconds)].iloc[0]
        raise RuntimeError(f"unsupported market timestamp magnitude {bad}")
    normalized_ms.loc[microseconds] = normalized_ms.loc[microseconds] // 1_000
    index = pd.to_datetime(normalized_ms, unit="ms", utc=True) + pd.Timedelta(
        minutes=5,
    )

    output = pd.DataFrame(index=index)
    for column in ("close", "quote_volume", "taker_buy_quote_volume"):
        output[column] = pd.to_numeric(raw[column], errors="coerce").to_numpy()
    output = output.dropna()
    output = output[~output.index.duplicated(keep="last")].sort_index()
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    output = output[(output.index > lower) & (output.index <= upper)]
    expected = int((upper - lower).total_seconds() // 300)
    coverage = len(output.index) / max(expected, 1)
    if coverage < 0.995:
        raise RuntimeError(
            f"insufficient market coverage: {len(output.index)}/{expected} "
            f"({coverage:.6f})",
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    v18.load_market_series = load_market_series
    v18.execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
