#!/usr/bin/env python3
"""Runtime-stable causal footprint augmentation.

This is the v6 state representation with per-minute path primitives computed
once, rather than rebuilding full arrays for every candidate action.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import mechanism_harvest_v6 as v6

base = v6.base
FEATURE_COLUMNS = v6.FEATURE_COLUMNS
SYMBOLS = base.SYMBOLS

_previous_prepare: Any = None
_previous_attach: Any = None
_previous_features: Any = None


def _prepare_symbol(frame: pd.DataFrame) -> pd.DataFrame:
    output = _previous_prepare(frame)
    output["close_change_1"] = output["close"].diff()
    output["signed_quote_1"] = (
        2.0 * output["taker_buy_quote_volume"] - output["quote_volume"]
    )
    return output


def _rank_panel(
    frames: dict[str, pd.DataFrame],
    column: str,
    output_column: str,
) -> None:
    panel = pd.concat(
        {
            symbol: frame.set_index("time")[column]
            for symbol, frame in frames.items()
        },
        axis=1,
    ).sort_index()
    ranks = panel.rank(axis=1, pct=True, method="average") - 0.5
    for symbol, frame in frames.items():
        times = pd.DatetimeIndex(frame["time"])
        frame[output_column] = ranks[symbol].reindex(times).to_numpy(float)


def _attach_panel(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    output = _previous_attach(frames)
    _rank_panel(output, "ret_3", "rank_ret_3")
    _rank_panel(output, "flow_3", "rank_flow_3")
    _rank_panel(output, "residual_z", "rank_residual")
    _rank_panel(output, "activity_z", "rank_activity")
    _rank_panel(output, "oi_change_5", "rank_oi_change")
    return output


def _fraction_aligned(values: np.ndarray, side: int) -> float:
    finite = values[np.isfinite(values)]
    if len(finite) == 0:
        return float("nan")
    return float(np.mean(side * finite > 0.0))


def _features(
    frame: pd.DataFrame,
    episode: base.Episode,
    snapshot_index: int,
    target: float,
    objective_rank: int,
    target_r: float,
) -> dict[str, float]:
    values = _previous_features(
        frame,
        episode,
        snapshot_index,
        target,
        objective_rank,
        target_r,
    )
    side = episode.side
    for lag in v6.LAGS:
        index = max(0, snapshot_index - lag)
        row = frame.iloc[index]
        values[f"aligned_ret_lag_{lag}"] = side * float(row.get("ret_1", np.nan))
        values[f"aligned_flow_lag_{lag}"] = side * float(row.get("flow_1", np.nan))

    current = frame.iloc[snapshot_index]
    values["aligned_return_acceleration"] = side * (
        float(current.get("ret_3", np.nan))
        - float(current.get("ret_15", np.nan)) / np.sqrt(5.0)
    )
    values["aligned_flow_acceleration"] = side * (
        float(current.get("flow_3", np.nan))
        - float(current.get("flow_15", np.nan))
    )
    for window in (5, 13):
        start = max(0, snapshot_index - window + 1)
        returns = frame["close_change_1"].iloc[start:snapshot_index + 1].to_numpy(float)
        flows = frame["signed_quote_1"].iloc[start:snapshot_index + 1].to_numpy(float)
        values[f"favorable_close_fraction_{window}"] = _fraction_aligned(returns, side)
        values[f"flow_sign_fraction_{window}"] = _fraction_aligned(flows, side)

    values["cross_rank_ret_3"] = side * float(current.get("rank_ret_3", np.nan))
    values["cross_rank_flow_3"] = side * float(current.get("rank_flow_3", np.nan))
    values["cross_rank_residual"] = side * float(current.get("rank_residual", np.nan))
    values["cross_rank_activity"] = float(current.get("rank_activity", np.nan))
    values["cross_rank_oi_change"] = float(current.get("rank_oi_change", np.nan))
    return values


def _install() -> None:
    global _previous_prepare, _previous_attach, _previous_features
    v6.v5._install()
    _previous_prepare = base._prepare_symbol
    _previous_attach = base._attach_panel
    _previous_features = base._features
    base.FEATURE_COLUMNS = FEATURE_COLUMNS
    base._prepare_symbol = _prepare_symbol
    base._attach_panel = _attach_panel
    base._features = _features


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", type=Path, default=Path(".cache/mechanism-v7"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    _install()
    args = parse_args()
    base.harvest(args.period, args.start, args.end, args.cache, args.output)


if __name__ == "__main__":
    main()
