#!/usr/bin/env python3
"""Aggregate target-frontier research windows into compact comparable evidence."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RISK_FRACTION = 0.03
EPS = 1e-12


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _metrics(frame: pd.DataFrame, days: int) -> dict[str, Any]:
    values = pd.to_numeric(frame.get("net_r", pd.Series(dtype=float)), errors="coerce").dropna()
    wins = values[values > 0.0]
    losses = values[values < 0.0]
    nav = peak = 1.0
    max_dd = 0.0
    for value in values:
        nav *= max(EPS, 1.0 + RISK_FRACTION * float(value))
        peak = max(peak, nav)
        max_dd = min(max_dd, nav / peak - 1.0)
    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    return {
        "trades": int(len(values)),
        "wins": int(len(wins)),
        "win_rate": float((values > 0.0).mean()) if len(values) else 0.0,
        "average_net_r": float(values.mean()) if len(values) else 0.0,
        "average_win_r": float(wins.mean()) if len(wins) else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0),
        "nav_final": float(nav),
        "max_drawdown": float(max_dd),
        "calendar_days": int(days),
        "trades_per_calendar_day": float(len(values) / days) if days else 0.0,
        "mean_target_net_r": float(pd.to_numeric(frame.get("target_net_r"), errors="coerce").mean()) if len(frame) else 0.0,
        "mean_gross_rr": float(pd.to_numeric(frame.get("gross_rr"), errors="coerce").mean()) if len(frame) else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    plan_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    rows: list[dict[str, Any]] = []
    manifests = sorted(args.root.rglob("target_policy_patch.json"))
    if not manifests:
        raise RuntimeError(f"no target-policy windows found below {args.root}")
    for manifest_path in manifests:
        folder = manifest_path.parent
        manifest = json.loads(manifest_path.read_text())
        variant = str(manifest["variant"])
        period = str(manifest["period"])
        role = str(manifest["role"])
        summary = json.loads((folder / "summary.json").read_text())
        start = str(summary["start"])
        end = str(summary["end"])
        days = max(1, (pd.Timestamp(end) - pd.Timestamp(start)).days)
        plans = pd.read_csv(folder / "candidate_plans.csv.gz")
        plans["target_policy_variant"] = variant
        plans["diagnostic_period"] = period
        plans["diagnostic_role"] = role
        plan_frames.append(plans)
        trades = pd.read_csv(folder / "global_trades.csv")
        trades["target_policy_variant"] = variant
        trades["diagnostic_period"] = period
        trades["diagnostic_role"] = role
        trade_frames.append(trades)
        row = {
            "variant": variant,
            "period": period,
            "role": role,
            "start": start,
            "end": end,
            "candidate_plans": int(summary.get("candidate_plans", len(plans))),
            "diagnosed_source_events": int(summary.get("diagnosed_source_events", 0)),
            **_metrics(trades, days),
        }
        rows.append(row)

    all_plans = pd.concat(plan_frames, ignore_index=True, sort=False)
    all_trades = pd.concat(trade_frames, ignore_index=True, sort=False)
    window_metrics = pd.DataFrame(rows).sort_values(["variant", "start"]).reset_index(drop=True)
    variant_summary: dict[str, Any] = {}
    for variant, group in all_trades.sort_values(["target_policy_variant", "entry_time_ns"]).groupby("target_policy_variant", sort=True):
        periods = window_metrics[window_metrics.variant.eq(variant)]
        variant_summary[str(variant)] = {
            **_metrics(group, int(periods.calendar_days.sum())),
            "candidate_plans": int((all_plans.target_policy_variant == variant).sum()),
            "positive_windows": int((periods.average_net_r > 0.0).sum()),
            "windows": int(len(periods)),
            "minimum_window_average_net_r": float(periods.average_net_r.min()),
        }
    summary = {
        "policy": "ML_FIRST_STRUCTURAL_TARGET_FRONTIER_RESEARCH_V3",
        "variants": variant_summary,
        "windows": int(len(window_metrics)),
        "candidate_plan_rows": int(len(all_plans)),
        "global_trade_rows": int(len(all_trades)),
    }
    all_plans.to_csv(args.output / "all_candidate_plans.csv.gz", index=False, compression="gzip")
    all_trades.to_csv(args.output / "all_global_trades.csv.gz", index=False, compression="gzip")
    window_metrics.to_csv(args.output / "window_metrics.csv", index=False)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=True) + "\n")
    print(json.dumps(summary, indent=2, allow_nan=True))


if __name__ == "__main__":
    main()
