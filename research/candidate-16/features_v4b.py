"""Candidate 16 v4b: repair L1 Parquet timestamp-unit alignment only.

The economic strategy, thresholds, evaluation week, and NautilusTrader execution
path are unchanged from v4. The v4 screen silently compared nanosecond Binance
feature keys with microsecond Parquet timestamp integers, so every L1 join missed.
This module normalizes timezone-aware Parquet timestamps to nanoseconds before
joining and fails closed when L1 coverage is insufficient.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any

import pandas as pd

import features_v4 as v4


MIN_L1_JOIN_COVERAGE = 0.95


def timestamp_series_to_ns(values: pd.Series) -> pd.Series:
    """Return UTC timestamps as explicit int64 nanoseconds.

    Arrow-backed Parquet timestamps can retain microsecond resolution. Calling
    ``astype('int64')`` directly then returns microseconds, not nanoseconds.
    ``dt.as_unit('ns')`` makes the unit explicit before integer conversion.
    """
    timestamp = pd.to_datetime(values, utc=True, errors="raise")
    return timestamp.dt.as_unit("ns").astype("int64")


def _load_l1(path: Path, start: date, end: date) -> pd.DataFrame:
    frame = pd.read_parquet(path, columns=list(v4.L1_COLUMNS))
    if tuple(frame.columns) != v4.L1_COLUMNS:
        raise RuntimeError(f"unexpected L1 columns: {list(frame.columns)}")
    if len(frame.index) != v4.DATASET_ROWS:
        raise RuntimeError(
            f"unexpected L1 row count: {len(frame.index)} != {v4.DATASET_ROWS}",
        )

    timestamp = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    timestamp_ns = timestamp_series_to_ns(frame["timestamp"])
    if timestamp_ns.duplicated().any() or not timestamp_ns.is_monotonic_increasing:
        order = timestamp_ns.argsort(kind="stable")
        frame = frame.iloc[order].reset_index(drop=True)
        timestamp = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
        timestamp_ns = timestamp_series_to_ns(frame["timestamp"])
    if timestamp_ns.duplicated().any() or not timestamp_ns.is_monotonic_increasing:
        raise RuntimeError("L1 timestamps are duplicated or non-monotonic")
    frame["minute_start_ns"] = timestamp_ns

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    selected = frame[(timestamp >= start_ts) & (timestamp < end_ts)].copy()
    if selected.empty:
        raise RuntimeError("no L1 pressure rows in requested build interval")
    numeric = [column for column in v4.L1_COLUMNS if column != "timestamp"]
    for column in numeric:
        selected[column] = pd.to_numeric(selected[column], errors="raise")
    required = (
        "bt_spread_bps_close",
        "bt_spread_bps_twap",
        "bt_imbalance_close",
        "bt_imbalance_twap",
        "bt_microprice_premium_close",
        "bt_update_rate",
    )
    selected["l1_pressure_feature_ready"] = (
        selected[list(required)].notna().all(axis=1)
        & selected["bt_spread_bps_close"].gt(0.0)
        & selected["bt_spread_bps_twap"].gt(0.0)
        & selected["bt_update_rate"].gt(0.0)
    )
    return selected.drop(columns=["timestamp"])


# Reuse v4's immutable download, checksum, evidence, and merge implementation,
# changing only its timestamp-unit conversion.
v4._load_l1 = _load_l1  # noqa: SLF001 - deliberate implementation repair


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
) -> tuple[pd.DataFrame, Path, list[Path], list[Any]]:
    result = v4.load_range(
        symbol=symbol,
        start=start,
        end=end,
        cache=cache,
        output=output,
    )
    feature_path = result[1]
    frame = pd.read_csv(feature_path, compression="infer")
    joined = frame["l1_pressure_feature_ready"].notna()
    ready = v4._as_bool(  # noqa: SLF001 - reuse frozen boolean contract
        frame["l1_pressure_feature_ready"].fillna(False),
    )
    coverage = float(joined.mean())
    diagnostics = {
        "schema": "candidate-16-v4b-l1-join-v1",
        "rows": int(len(frame.index)),
        "joined_l1_rows": int(joined.sum()),
        "ready_l1_rows": int(ready.sum()),
        "l1_join_coverage": coverage,
        "strategy_feature_ready_rows": int(
            v4._as_bool(frame["feature_ready"]).sum()  # noqa: SLF001
        ),
        "timestamp_unit": "nanoseconds",
        "economic_rules_changed": False,
    }
    (output / "candidate16_v4b_l1_join.json").write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if coverage < MIN_L1_JOIN_COVERAGE:
        raise RuntimeError(
            "L1 join coverage below fail-closed minimum: "
            f"{coverage:.6f} < {MIN_L1_JOIN_COVERAGE:.6f}",
        )
    if diagnostics["strategy_feature_ready_rows"] <= 0:
        raise RuntimeError("no strategy-ready rows after repaired L1 join")
    return result


__all__ = [
    "MIN_L1_JOIN_COVERAGE",
    "load_range",
    "timestamp_series_to_ns",
]
