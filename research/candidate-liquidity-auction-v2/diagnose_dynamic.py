#!/usr/bin/env python3
"""Compact mechanism diagnosis for horizontal versus dynamic liquidity episodes."""
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
        raise RuntimeError(f"no coherent action universes under {root}")
    frame = pd.concat(pieces, ignore_index=True, sort=False)
    if frame.duplicated(["period", "action_id"]).any():
        raise RuntimeError("duplicate action identity")
    return frame


def _safe_mean(series: pd.Series) -> float | None:
    value = pd.to_numeric(series, errors="coerce").mean()
    return float(value) if pd.notna(value) else None


def _describe(group: pd.DataFrame) -> dict[str, Any]:
    filled = group[group.fill_state.astype(str).str.startswith("FILLED")]
    resolved = filled[filled.outcome.astype(str).isin(RESOLVED)]
    return {
        "actions": int(len(group)),
        "episodes": int(group.episode_id.nunique()),
        "filled": int(len(filled)),
        "fill_rate": float(len(filled) / len(group)) if len(group) else None,
        "resolved": int(len(resolved)),
        "target_first": int(resolved.outcome.astype(str).eq("TARGET_FIRST").sum()),
        "target_first_rate": float(
            resolved.outcome.astype(str).eq("TARGET_FIRST").mean()
        )
        if len(resolved)
        else None,
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
    }


def _group(frame: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, group in frame.groupby(columns, dropna=False, observed=True):
        if not isinstance(key, tuple):
            key = (key,)
        token = " | ".join(str(value) for value in key)
        output[token] = _describe(group)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    frame = _read(args.root)
    frame["source_family"] = np.where(
        pd.to_numeric(frame.get("dynamic_source_present", 0.0), errors="coerce")
        .fillna(0.0)
        .gt(0.0),
        np.where(
            pd.to_numeric(frame.get("dynamic_source_is_channel", 0.0), errors="coerce")
            .fillna(0.0)
            .gt(0.0),
            "DYNAMIC_CHANNEL",
            "DYNAMIC_TRENDLINE",
        ),
        "HORIZONTAL_LIQUIDITY",
    )
    frame["objective_family"] = np.select(
        [
            pd.to_numeric(
                frame.get("route_obstacle_is_dynamic_diagonal", 0.0),
                errors="coerce",
            )
            .fillna(0.0)
            .gt(0.0),
            pd.to_numeric(
                frame.get("route_obstacle_is_multiscale_volume", 0.0),
                errors="coerce",
            )
            .fillna(0.0)
            .gt(0.0),
        ],
        ["DYNAMIC_DIAGONAL", "VOLUME_NODE"],
        default="STATIC_LIQUIDITY",
    )
    summary = {
        "overall": _describe(frame),
        "periods": _group(frame, ["period"]),
        "source_family": _group(frame, ["source_family"]),
        "source_branch": _group(frame, ["source_family", "narrative_branch"]),
        "source_entry": _group(frame, ["source_family", "entry_geometry"]),
        "source_stop": _group(frame, ["source_family", "stop_geometry"]),
        "objective_family": _group(frame, ["objective_family"]),
        "source_objective": _group(
            frame,
            ["source_family", "objective_family"],
        ),
        "source_kind": _group(frame, ["source_kind"]),
        "action_universe": int(len(frame)),
        "episode_universe": int(frame.episode_id.nunique()),
        "period_count": int(frame.period.nunique()),
    }
    (args.output / "dynamic_diagnosis.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    compact = pd.DataFrame(
        [
            {
                "period": row.period,
                "action_id": row.action_id,
                "episode_id": row.episode_id,
                "symbol": row.symbol,
                "side": row.side,
                "source_family": row.source_family,
                "source_kind": row.source_kind,
                "narrative_branch": row.narrative_branch,
                "entry_geometry": row.entry_geometry,
                "stop_geometry": row.stop_geometry,
                "objective_family": row.objective_family,
                "objective_kind": row.objective_kind,
                "gross_rr": row.gross_rr,
                "planned_account_target_r": row.planned_account_target_r,
                "fill_state": row.fill_state,
                "outcome": row.outcome,
                "net_r": row.net_r,
                "holding_minutes": row.holding_minutes,
            }
            for row in frame.itertuples()
        ]
    )
    compact.to_csv(args.output / "dynamic_action_compact.csv", index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
