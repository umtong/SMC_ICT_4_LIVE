#!/usr/bin/env python3
"""Causal selector wrapper: no actual-fill features and no future episode arbitration."""
from __future__ import annotations

import pandas as pd

import train_select as base

_ORIGINAL_FEATURE_COLUMNS = base._feature_columns


def causal_feature_columns(frame: pd.DataFrame, *, keep_economics: bool) -> list[str]:
    columns = _ORIGINAL_FEATURE_COLUMNS(frame, keep_economics=keep_economics)
    forbidden_prefixes = (
        "actual_",
        "fill_",
        "resolution_",
        "destination_resolution_",
    )
    return [
        column for column in columns
        if not str(column).lower().startswith(forbidden_prefixes)
    ]


def causal_one_account(actions: pd.DataFrame, subset: str):
    frame = actions[
        actions.period.astype(str).str.startswith(subset)
        & actions.fill_state.astype(str).eq("FILLED_MARKET_NEXT_OPEN")
        & pd.to_numeric(actions.robust_expected_r, errors="coerce").gt(0.0)
        & actions.outcome.astype(str).isin(
            ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "TIME_EXIT"]
        )
    ].copy()
    if frame.empty:
        return frame, {
            "trades": 0,
            "wins": 0,
            "win_rate": None,
            "mean_net_r": None,
            "ending_nav": 100_000.0,
            "maximum_drawdown": 0.0,
        }
    frame = frame.sort_values(
        ["fill_time_ns", "expected_log_growth", "source_timeframe_minutes", "action_id"],
        ascending=[True, False, False, True],
    )
    selected: list[pd.Series] = []
    occupied_episodes: set[tuple[str, str]] = set()
    busy_until = -1
    for fill_time, group in frame.groupby("fill_time_ns", sort=True):
        fill_time = int(fill_time)
        if fill_time <= busy_until:
            continue
        mask = [
            (str(row.period), str(row.episode_id)) not in occupied_episodes
            for row in group.itertuples()
        ]
        available = group.loc[mask]
        if available.empty:
            continue
        candidate = available.sort_values(
            ["expected_log_growth", "robust_expected_r", "source_timeframe_minutes", "action_id"],
            ascending=[False, False, False, True],
        ).iloc[0]
        selected.append(candidate)
        occupied_episodes.add((str(candidate.period), str(candidate.episode_id)))
        busy_until = int(candidate.resolution_time_ns)
    output = pd.DataFrame(selected).reset_index(drop=True) if selected else frame.iloc[0:0].copy()
    nav = 100_000.0
    peak = nav
    max_drawdown = 0.0
    nav_before: list[float] = []
    nav_after: list[float] = []
    for _, row in output.iterrows():
        nav_before.append(nav)
        result = base._safe_float(row.net_r, 0.0)
        nav *= max(1e-9, 1.0 + base.RISK_FRACTION * result)
        peak = max(peak, nav)
        max_drawdown = max(max_drawdown, 1.0 - nav / peak)
        nav_after.append(nav)
    output["nav_before"] = nav_before
    output["nav_after"] = nav_after
    wins = int(output.outcome.astype(str).eq("TARGET_FIRST").sum())
    result = pd.to_numeric(output.net_r, errors="coerce")
    summary = {
        "trades": int(len(output)),
        "wins": wins,
        "win_rate": wins / len(output) if len(output) else None,
        "mean_net_r": float(result.mean()) if len(output) else None,
        "median_net_r": float(result.median()) if len(output) else None,
        "ending_nav": float(nav),
        "maximum_drawdown": float(max_drawdown),
        "by_period": {
            str(period): {
                "trades": int(len(group)),
                "wins": int(group.outcome.astype(str).eq("TARGET_FIRST").sum()),
                "win_rate": float(group.outcome.astype(str).eq("TARGET_FIRST").mean()),
                "mean_net_r": float(pd.to_numeric(group.net_r, errors="coerce").mean()),
            }
            for period, group in output.groupby("period")
        },
        "by_branch": {
            str(branch): {
                "trades": int(len(group)),
                "win_rate": float(group.outcome.astype(str).eq("TARGET_FIRST").mean()),
                "mean_net_r": float(pd.to_numeric(group.net_r, errors="coerce").mean()),
            }
            for branch, group in output.groupby("narrative_branch")
        },
    }
    return output, summary


base._feature_columns = causal_feature_columns
base._one_account = causal_one_account

if __name__ == "__main__":
    base.main()
