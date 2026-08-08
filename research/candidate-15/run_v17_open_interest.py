#!/usr/bin/env python3
"""Run V17 with a robust adapter for Binance daily metrics CSV timestamps.

The first V17 execution established that Binance daily metrics store
``create_time`` as a UTC datetime string rather than a Unix integer.  This
adapter changes only file parsing.  The frozen V17 state rules, horizons,
costs, arbitration and evaluation dates remain untouched.
"""
from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pandas as pd

import diagnose_v17_open_interest as v17


def read_metric_archive(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise RuntimeError(f"unexpected metrics members in {path}: {members}")
        payload = archive.read(members[0])

    # Read positionally so BOMs and header/no-header variants cannot silently
    # convert every observation into a missing timestamp.
    frame = pd.read_csv(BytesIO(payload), header=None, dtype=str)
    if frame.shape[1] < len(v17.METRIC_COLUMNS):
        raise RuntimeError(
            f"unexpected metrics column count in {path}: {frame.shape[1]}",
        )
    frame = frame.iloc[:, : len(v17.METRIC_COLUMNS)].copy()
    frame.columns = v17.METRIC_COLUMNS
    create_time = (
        frame["create_time"]
        .astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )
    frame = frame[create_time.str.lower() != "create_time"].copy()
    frame["create_time"] = create_time[create_time.str.lower() != "create_time"]
    return frame


def load_metrics(paths: list[Path], start: Any, end: Any) -> pd.DataFrame:
    if not paths:
        raise RuntimeError("no metric archives")
    raw = pd.concat(
        [read_metric_archive(path) for path in paths],
        ignore_index=True,
    )
    if raw.empty:
        raise RuntimeError(f"no metric rows decoded from {paths[0]}")

    text = raw["create_time"].astype(str).str.strip()
    numeric = pd.to_numeric(text, errors="coerce")
    if numeric.notna().mean() >= 0.95:
        integer = numeric.astype("int64")
        first = int(integer.dropna().iloc[0])
        if 1_000_000_000 <= first < 10_000_000_000:
            unit = "s"
        elif 1_000_000_000_000 <= first < 10_000_000_000_000:
            unit = "ms"
        elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
            unit = "us"
        else:
            raise RuntimeError(f"unsupported metric timestamp magnitude {first}")
        timestamps = pd.to_datetime(integer, unit=unit, utc=True)
    else:
        timestamps = pd.to_datetime(text, utc=True, errors="coerce")

    valid = timestamps.notna()
    raw = raw.loc[valid].copy()
    timestamps = pd.DatetimeIndex(timestamps[valid])
    if raw.empty:
        raise RuntimeError(f"no valid metric timestamps decoded from {paths[0]}")

    raw["_timestamp"] = timestamps.to_numpy()
    raw = raw.drop_duplicates("_timestamp", keep="last").sort_values("_timestamp")
    output = pd.DataFrame(index=pd.DatetimeIndex(raw.pop("_timestamp"), tz="UTC"))
    for column in v17.METRIC_COLUMNS[2:]:
        output[column] = pd.to_numeric(raw[column], errors="coerce").to_numpy()
    output = output.dropna(subset=list(v17.METRIC_COLUMNS[2:]))
    output = output[~output.index.duplicated(keep="last")].sort_index()

    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    output = output[(output.index > lower) & (output.index <= upper)]
    expected = int((upper - lower).total_seconds() // 300)
    coverage = len(output.index) / max(expected, 1)
    if coverage < 0.97:
        raise RuntimeError(
            f"insufficient metrics coverage: {len(output.index)}/{expected} "
            f"({coverage:.6f})",
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    # Data-adapter correction only. No strategy contract is modified.
    v17.read_metric_archive = read_metric_archive
    v17.load_metrics = load_metrics
    v17.execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
