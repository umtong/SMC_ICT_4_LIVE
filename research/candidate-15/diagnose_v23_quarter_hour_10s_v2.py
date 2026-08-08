#!/usr/bin/env python3
"""Causal corrections for Candidate 15 V23.

This wrapper preserves the frozen V23 state thresholds while correcting two
implementation contracts before any result is interpreted:

1. "same phase" means the same :00/:15/:30/:45 clock phase, not merely the
   previous adjacent quarter-hour event;
2. every economic outcome starts at the post-confirmation entry close, never
   at the boundary close.

The underlying downloader, feature construction, arbitration, reporting and
split gates remain in diagnose_v23_quarter_hour_10s.py.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import diagnose_v23_quarter_hour_10s as base


_ORIGINAL_ADD_EVENT_CONTEXT = base.add_event_context
_ORIGINAL_CANDIDATE_ROWS = base.candidate_rows


def add_event_context(
    events: pd.DataFrame,
    minute_features: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    output = _ORIGINAL_ADD_EVENT_CONTEXT(events, minute_features, rules)
    if output.empty:
        return output
    entry_index = pd.DatetimeIndex(
        pd.to_datetime(output["entry_ts"], utc=True)
    )
    output["entry_target_240m"] = (
        minute_features["target_240m"].reindex(entry_index).to_numpy()
    )
    output["entry_target_480m"] = (
        minute_features["target_480m"].reindex(entry_index).to_numpy()
    )
    output["outcome_origin"] = "POST_CONFIRMATION_ENTRY_CLOSE"
    return output


def add_prior_boundary_state(
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    output = frame.sort_index(kind="stable").copy()
    absolute = output["ten_second_imbalance"].abs()
    output["imbalance_threshold"] = (
        absolute.shift(1)
        .rolling(
            int(rules["absolute_imbalance_prior_lookback_events"]),
            min_periods=int(
                rules["absolute_imbalance_prior_minimum_events"]
            ),
        )
        .quantile(float(rules["absolute_imbalance_prior_quantile"]))
    )

    phase = pd.Series(
        pd.DatetimeIndex(output.index).minute,
        index=output.index,
    )
    current_sign = np.sign(
        output["ten_second_imbalance"].astype(float)
    ).replace(0.0, np.nan)
    event_times = pd.Series(
        pd.DatetimeIndex(output.index),
        index=output.index,
    )
    lag_count = int(rules["same_phase_lag_events"])
    lag_values = pd.concat(
        [
            current_sign.groupby(phase).shift(lag)
            for lag in range(1, lag_count + 1)
        ],
        axis=1,
    )
    lag_times = pd.concat(
        [
            event_times.groupby(phase).shift(lag)
            for lag in range(1, lag_count + 1)
        ],
        axis=1,
    )
    lag_values.columns = [
        f"same_phase_sign_lag_{lag}"
        for lag in range(1, lag_count + 1)
    ]
    lag_times.columns = [
        f"same_phase_ts_lag_{lag}"
        for lag in range(1, lag_count + 1)
    ]

    history_limit = pd.Timedelta(
        hours=float(rules["same_phase_history_hours_max"])
    )
    oldest_age = event_times - pd.to_datetime(
        lag_times.iloc[:, -1],
        utc=True,
    )
    history_complete = lag_values.notna().sum(axis=1) == lag_count
    history_fresh = history_complete & oldest_age.le(history_limit)
    history_agreement = lag_values.eq(current_sign, axis=0).mean(axis=1)
    output["same_phase_direction_agreement"] = history_agreement.where(
        history_fresh
    )
    output["same_phase_history_fresh"] = history_fresh
    output["same_phase_oldest_age_minutes"] = (
        oldest_age.dt.total_seconds() / 60.0
    )

    fresh_count = int(rules["same_phase_fresh_lag_events"])
    fresh_values = lag_values.iloc[:, :fresh_count]
    fresh_times = lag_times.iloc[:, :fresh_count]
    fresh_limit = pd.Timedelta(
        hours=float(rules["same_phase_freshness_hours_max"])
    )
    ages = fresh_times.apply(
        lambda column: event_times - pd.to_datetime(column, utc=True)
    )
    fresh_mask = ages.le(fresh_limit) & fresh_values.notna()
    usable = fresh_values.where(fresh_mask)
    denominator = usable.notna().sum(axis=1)
    output["fresh_phase_direction_agreement"] = (
        usable.eq(current_sign, axis=0).sum(axis=1)
        / denominator.replace(0, np.nan)
    )
    output["fresh_phase_observations"] = denominator
    output.loc[
        denominator < fresh_count,
        "fresh_phase_direction_agreement",
    ] = np.nan
    output["phase_minute"] = phase
    return output


def candidate_rows(
    symbol: str,
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    corrected = frame.copy()
    corrected["target_240m"] = corrected["entry_target_240m"]
    corrected["target_480m"] = corrected["entry_target_480m"]
    rows = _ORIGINAL_CANDIDATE_ROWS(symbol, corrected, rules)
    if not rows.empty:
        rows["outcome_origin"] = "POST_CONFIRMATION_ENTRY_CLOSE"
        rows["same_phase_definition"] = (
            "SAME_CLOCK_PHASE_WITH_FRESHNESS"
        )
    return rows


base.add_event_context = add_event_context
base.add_prior_boundary_state = add_prior_boundary_state
base.candidate_rows = candidate_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base.execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
