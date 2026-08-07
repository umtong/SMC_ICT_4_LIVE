#!/usr/bin/env python3
"""Shared-account runner with UTC-day closing NAV aligned to the same day."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import shared_account_backtest as _base


PROJECT_SYMBOLS = _base.PROJECT_SYMBOLS
SharedAccountError = _base.SharedAccountError
write_json = _base.write_json


def normalize_equity_files(
    *,
    output: Path,
    evaluation_start: date,
    evaluation_end: date,
    starting_nav: float,
    ending_nav: float,
):
    """Use each UTC day's final in-day shared NAV, never the next day's first."""
    frames: list[pd.DataFrame] = []
    symbol_order = {symbol: index for index, symbol in enumerate(PROJECT_SYMBOLS)}
    for symbol in PROJECT_SYMBOLS:
        path = output / "symbols" / symbol / "equity.csv"
        if not path.exists() or path.stat().st_size == 0:
            raise SharedAccountError(f"missing strategy equity observations: {symbol}")
        frame = pd.read_csv(path)
        if not {"ts_event", "equity"}.issubset(frame.columns):
            raise SharedAccountError(f"invalid equity schema for {symbol}: {list(frame.columns)}")
        frame = frame[["ts_event", "equity"]].copy()
        frame["ts_event"] = pd.to_numeric(frame["ts_event"], errors="raise").astype("int64")
        frame["equity"] = pd.to_numeric(frame["equity"], errors="raise").astype(float)
        frame["symbol"] = symbol
        frame["symbol_order"] = symbol_order[symbol]
        frame["row_order"] = np.arange(len(frame), dtype=np.int64)
        frame["time"] = pd.to_datetime(frame["ts_event"], unit="ns", utc=True)
        frames.append(frame)

    equity = pd.concat(frames, ignore_index=True).sort_values(
        ["ts_event", "symbol_order", "row_order"],
        kind="stable",
    )
    start_ts = pd.Timestamp(evaluation_start, tz="UTC")
    final_boundary = pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")
    selected = equity[
        (equity["time"] >= start_ts) & (equity["time"] < final_boundary)
    ].copy()
    if selected.empty:
        raise SharedAccountError("no shared equity observations in evaluation range")

    cursor = float(starting_nav)
    daily_returns: dict[str, float] = {}
    for offset in range((evaluation_end - evaluation_start).days + 1):
        day = evaluation_start + timedelta(days=offset)
        day_start = pd.Timestamp(day, tz="UTC")
        boundary = pd.Timestamp(day + timedelta(days=1), tz="UTC")
        if day == evaluation_end:
            close = float(ending_nav)
        else:
            inside_day = selected[
                (selected["time"] >= day_start) & (selected["time"] < boundary)
            ]
            close = cursor if inside_day.empty else float(inside_day.iloc[-1]["equity"])
        daily_returns[str(day)] = close / cursor - 1.0
        cursor = close

    trajectory = pd.concat(
        [
            pd.Series([float(starting_nav)], dtype=float),
            selected["equity"].astype(float).reset_index(drop=True),
            pd.Series([float(ending_nav)], dtype=float),
        ],
        ignore_index=True,
    )
    peaks = trajectory.cummax()
    max_drawdown = float((1.0 - trajectory / peaks).max())
    min_equity = float(trajectory.min())
    selected.to_csv(output / "shared_equity_observations.csv", index=False)
    write_json(output / "daily_returns.json", daily_returns)
    return selected, daily_returns, max_drawdown, min_equity


# Patch only the reporting calculation. Market replay, orders, fills, fees,
# positions, margin, liquidation and NAV remain owned by the original
# NautilusTrader BacktestNode implementation.
_base.normalize_equity_files = normalize_equity_files


def main() -> None:
    _base.main()


if __name__ == "__main__":
    main()
