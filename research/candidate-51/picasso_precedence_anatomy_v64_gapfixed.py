#!/usr/bin/env python3
"""Gap-aware runner for the frozen Picasso v64 experiment.

The original v64 logic is unchanged.  Two input-contract issues are corrected:

* historical Binance hourly archives contain a small number of missing hours;
  prices are not synthesized, indicators reset after every gap, and no trade
  path may cross a gap;
* the most recent completed evaluation is ended early enough to retain the
  frozen 14-day maximum forward path, rather than requesting future archives.

The first two v64 workflows failed before strategy results because of JSON
serialization and strict completeness, so the periods have not been used to
select any threshold or policy.
"""
from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timedelta
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
GAP_DIAGNOSTICS: dict[str, dict[str, Any]] = {}


def _load_v64():
    path = HERE / "picasso_precedence_anatomy_v64.py"
    spec = importlib.util.spec_from_file_location(
        "candidate51_picasso_v64_gapfixed", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V64 = _load_v64()


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    raise TypeError(type(value))


def _gap_load_hourly(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
):
    frames: list[pd.DataFrame] = []
    evidence = []
    unavailable_archives: list[str] = []
    for month in V64._month_starts(start, end):
        month_end = date(
            month.year,
            month.month,
            calendar.monthrange(month.year, month.month)[1],
        )
        overlap_start = max(start, month)
        overlap_end = min(end, month_end)
        stamp = f"{month.year:04d}-{month.month:02d}"
        try:
            archive, item = V64._checked_archive(
                symbol=symbol,
                interval="1h",
                source_mode="monthly",
                stamp=stamp,
                cache=cache,
            )
            frames.append(V64._read_kline(archive))
            evidence.append(item)
            continue
        except HTTPError as error:
            if error.code != 404:
                raise
            unavailable_archives.append(
                f"monthly:{symbol}:1h:{stamp}"
            )
        day = overlap_start
        while day <= overlap_end:
            try:
                archive, item = V64._checked_archive(
                    symbol=symbol,
                    interval="1h",
                    source_mode="daily",
                    stamp=day.isoformat(),
                    cache=cache,
                )
                frames.append(V64._read_kline(archive))
                evidence.append(item)
            except HTTPError as error:
                if error.code != 404:
                    raise
                unavailable_archives.append(
                    f"daily:{symbol}:1h:{day.isoformat()}"
                )
            day += timedelta(days=1)
    if not frames:
        raise RuntimeError(f"no observed hourly data for {symbol}")
    frame = pd.concat(frames, ignore_index=True)
    frame = (
        frame.drop_duplicates("open_time_dt", keep="last")
        .sort_values("open_time_dt")
        .reset_index(drop=True)
    )
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    frame = frame[
        (frame["open_time_dt"] >= lower)
        & (frame["open_time_dt"] < upper)
    ].reset_index(drop=True)
    times = pd.DatetimeIndex(frame["open_time_dt"])
    deltas = times.to_series(index=frame.index).diff()
    frame["segment_id"] = deltas.ne(pd.Timedelta(hours=1)).cumsum().astype(int)
    expected_index = pd.date_range(
        lower,
        upper - pd.Timedelta(hours=1),
        freq="1h",
    )
    missing = expected_index.difference(times)
    GAP_DIAGNOSTICS[symbol] = {
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "observed_rows": int(len(frame)),
        "expected_rows": int(len(expected_index)),
        "missing_hours": int(len(missing)),
        "missing_timestamps": [value.isoformat() for value in missing],
        "contiguous_segments": int(frame["segment_id"].nunique()),
        "unavailable_archives": unavailable_archives,
        "policy": (
            "no synthesis; indicator warmup resets after each gap and trade "
            "paths are confined to one contiguous segment"
        ),
    }
    return frame, evidence


def _gap_features(frame: pd.DataFrame) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for segment_id, group in frame.groupby("segment_id", sort=True):
        source = group.drop(columns=["segment_id"]).reset_index(drop=True)
        enriched = V64._features(source)
        enriched["segment_id"] = int(segment_id)
        pieces.append(enriched)
    if not pieces:
        return frame.iloc[0:0].copy()
    return pd.concat(pieces, ignore_index=True)


def _gap_signals(
    *,
    symbol: str,
    frame: pd.DataFrame,
    start: date,
    end: date,
    period_label: str,
    split: str,
):
    records: list[dict[str, Any]] = []
    for segment_id, group in frame.groupby("segment_id", sort=True):
        source = group.drop(columns=["segment_id"]).reset_index(drop=True)
        item = V64._signals(
            symbol=symbol,
            frame=source,
            start=start,
            end=end,
            period_label=period_label,
            split=split,
        )
        for row in item:
            row["source_segment_id"] = int(segment_id)
        records.extend(item)
    return records


def run_one(args: argparse.Namespace) -> None:
    GAP_DIAGNOSTICS.clear()
    V64._json_default = _json_default
    V64._load_hourly = _gap_load_hourly
    V64._features = _gap_features
    V64._signals = _gap_signals
    V64.run_one(args)
    path = Path(args.output) / "result.json"
    payload = json.loads(path.read_text())
    payload["gap_diagnostics"] = GAP_DIAGNOSTICS
    payload["input_correction"] = {
        "strategy_change": "none",
        "threshold_change": "none",
        "historical_gaps": (
            "observed only; indicators and paths reset at every hourly gap"
        ),
        "recent_period": (
            "workflow end date leaves the frozen 14-day maximum forward "
            "path fully observable"
        ),
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default)
        + "\n"
    )


def aggregate(args: argparse.Namespace) -> None:
    V64._json_default = _json_default
    V64.aggregate(args)
    path = Path(args.output) / "ANATOMY.json"
    payload = json.loads(path.read_text())
    payload["input_correction"] = {
        "strategy_change": "none",
        "threshold_change": "none",
        "historical_gaps": (
            "observed only; indicators and paths reset at every hourly gap"
        ),
        "recent_period": (
            "ended 2026-07-26 so the frozen 14-day forward path ends by "
            "2026-08-09"
        ),
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default)
        + "\n"
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("start", "end", "period_label", "split", "output"):
        run.add_argument(
            f"--{name.replace('_', '-')}", dest=name, required=True
        )
    run.add_argument("--cache", default=".cache/candidate-51-picasso-v64b")
    run.set_defaults(func=run_one)
    aggregate_parser = sub.add_parser("aggregate")
    aggregate_parser.add_argument("--results-root", required=True)
    aggregate_parser.add_argument("--output", required=True)
    aggregate_parser.set_defaults(func=aggregate)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
