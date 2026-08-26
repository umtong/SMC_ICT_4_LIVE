#!/usr/bin/env python3
"""Mechanism diagnosis for the online episode-state census."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
import json
import re

import numpy as np
import pandas as pd

RESOLVED = {
    "TARGET_FIRST",
    "STOP_FIRST",
    "AMBIGUOUS_SAME_MINUTE",
    "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
    "TIME_EXIT",
}


def _period(path: Path) -> str:
    for part in reversed(path.parts):
        match = re.search(r"((?:dev|eval)-\d{4}-[a-z]{3})", part.lower())
        if match:
            return match.group(1)
    raise ValueError(path)


def _read(root: Path) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for path in sorted(root.glob("**/coherent_actions.csv")):
        frame = pd.read_csv(path)
        if frame.empty or "action_id" not in frame:
            continue
        frame["period"] = _period(path)
        pieces.append(frame)
    if not pieces:
        raise RuntimeError(f"no episode-state actions under {root}")
    frame = pd.concat(pieces, ignore_index=True, sort=False)
    if frame.duplicated(["period", "action_id"]).any():
        raise RuntimeError("duplicate period/action identity")
    return frame


def _number(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(0.0, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def _safe_mean(series: pd.Series) -> float | None:
    value = pd.to_numeric(series, errors="coerce").mean()
    return float(value) if pd.notna(value) else None


def _describe(group: pd.DataFrame) -> dict[str, Any]:
    filled = group[group.fill_state.astype(str).str.startswith("FILLED")]
    resolved = filled[filled.outcome.astype(str).isin(RESOLVED)]
    wins = resolved.outcome.astype(str).eq("TARGET_FIRST")
    return {
        "actions": int(len(group)),
        "episodes": int(group.episode_id.nunique()),
        "filled": int(len(filled)),
        "resolved": int(len(resolved)),
        "win_rate": float(wins.mean()) if len(resolved) else None,
        "mean_net_r": _safe_mean(resolved.net_r) if len(resolved) else None,
        "median_net_r": float(pd.to_numeric(resolved.net_r, errors="coerce").median())
        if len(resolved)
        else None,
        "mean_planned_target_r": _safe_mean(group.planned_account_target_r),
        "median_planned_target_r": float(
            pd.to_numeric(group.planned_account_target_r, errors="coerce").median()
        ),
        "mean_holding_minutes": _safe_mean(resolved.holding_minutes)
        if len(resolved)
        else None,
        "mean_episode_age": _safe_mean(group.episode_age_minutes),
    }


def _group(frame: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, group in frame.groupby(columns, dropna=False, observed=True):
        if not isinstance(key, tuple):
            key = (key,)
        output[" | ".join(str(value) for value in key)] = _describe(group)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    frame = _read(args.root)
    frame["source_family"] = np.where(
        _number(frame, "dynamic_source_present").gt(0.0),
        np.where(
            _number(frame, "dynamic_source_is_channel").gt(0.0),
            "DYNAMIC_CHANNEL",
            "DYNAMIC_TRENDLINE",
        ),
        "HORIZONTAL_LIQUIDITY",
    )
    frame["age_bucket"] = pd.cut(
        _number(frame, "episode_age_minutes"),
        bins=[-0.1, 3, 6, 10, 15, 24, 36, 60],
        labels=["1-3", "4-6", "7-10", "11-15", "16-24", "25-36", "37-60"],
        include_lowest=True,
    ).astype(str)
    frame["planned_r_bucket"] = pd.cut(
        pd.to_numeric(frame.planned_account_target_r, errors="coerce"),
        bins=[-np.inf, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, np.inf],
        labels=["<0.75", "0.75-1", "1-1.25", "1.25-1.5", "1.5-2", "2-3", ">=3"],
    ).astype(str)
    summary = {
        "overall": _describe(frame),
        "period": _group(frame, ["period"]),
        "branch": _group(frame, ["narrative_branch"]),
        "branch_age": _group(frame, ["narrative_branch", "age_bucket"]),
        "source_family": _group(frame, ["source_family"]),
        "source_branch": _group(frame, ["source_family", "narrative_branch"]),
        "response": _group(frame, ["response_kind"]),
        "stop": _group(frame, ["stop_geometry"]),
        "objective": _group(frame, ["objective_kind"]),
        "planned_r": _group(frame, ["planned_r_bucket"]),
        "branch_planned_r": _group(
            frame,
            ["narrative_branch", "planned_r_bucket"],
        ),
        "action_universe": int(len(frame)),
        "episode_universe": int(frame.episode_id.nunique()),
        "states_per_episode": float(len(frame) / max(frame.episode_id.nunique(), 1)),
    }
    (args.output / "episode_state_diagnosis.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    compact_columns = [
        "period",
        "action_id",
        "episode_id",
        "symbol",
        "side",
        "narrative_branch",
        "response_kind",
        "source_family",
        "source_kind",
        "source_pool_kind",
        "age_bucket",
        "episode_age_minutes",
        "stop_geometry",
        "objective_kind",
        "planned_account_target_r",
        "gross_rr",
        "fill_state",
        "outcome",
        "net_r",
        "holding_minutes",
    ]
    frame.loc[:, compact_columns].to_csv(
        args.output / "episode_state_compact.csv",
        index=False,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
