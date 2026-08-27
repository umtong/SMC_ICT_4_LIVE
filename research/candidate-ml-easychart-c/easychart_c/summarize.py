#!/usr/bin/env python3
"""Aggregate non-overlapping short evaluations as one chronological R-account."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RISK_FRACTION = 0.03


def drawdown(nav: np.ndarray) -> float:
    if nav.size == 0:
        return 0.0
    peak = np.maximum.accumulate(nav)
    return float(np.min(nav / peak - 1.0))


def metrics(frame: pd.DataFrame, calendar_days: int) -> dict[str, Any]:
    if frame.empty:
        return {
            "trades": 0,
            "calendar_days": calendar_days,
            "trades_per_day": 0.0,
            "win_rate": None,
            "mean_net_r": None,
            "profit_factor": None,
            "nav_multiple": 1.0,
            "max_drawdown": 0.0,
            "average_planned_rr": None,
            "average_hold_minutes": None,
        }
    net = pd.to_numeric(frame["actual_net_r"], errors="raise").to_numpy(float)
    factors = 1.0 + RISK_FRACTION * net
    if np.any(factors <= 0.0):
        raise RuntimeError("an evaluated trade makes the fixed-risk NAV non-positive")
    nav = np.cumprod(factors)
    positives = net[net > 0.0].sum()
    negatives = -net[net < 0.0].sum()
    return {
        "trades": int(len(frame)),
        "calendar_days": int(calendar_days),
        "trades_per_day": float(len(frame) / calendar_days),
        "win_rate": float(np.mean(net > 0.0)),
        "mean_net_r": float(np.mean(net)),
        "profit_factor": float(positives / negatives) if negatives > 0 else None,
        "nav_multiple": float(nav[-1]),
        "max_drawdown": drawdown(nav),
        "average_planned_rr": float(
            pd.to_numeric(frame["planned_gross_rr"], errors="coerce").mean()
        ),
        "average_hold_minutes": float(
            pd.to_numeric(frame["duration_ns"], errors="coerce").mean()
            / 60_000_000_000
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    audits: list[pd.DataFrame] = []
    windows: list[dict[str, Any]] = []
    for audit_path in sorted(args.evaluation_root.rglob("trade_audit.csv")):
        metrics_path = audit_path.with_name("metrics.json")
        if not metrics_path.exists():
            raise RuntimeError(f"missing metrics.json beside {audit_path}")
        run_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        period = audit_path.parent.name.removeprefix("easychart-c-eval-")
        audit = pd.read_csv(audit_path, low_memory=False)
        audit["evaluation_period"] = period
        audit["ts_opened_sort"] = pd.to_datetime(audit["ts_opened"], utc=True)
        audits.append(audit)
        window_metrics = metrics(audit, int(run_metrics["calendar_days"]))
        window_metrics.update(
            {
                "period": period,
                "start": run_metrics.get("start"),
                "end": run_metrics.get("end"),
                "final_nav_reported": run_metrics.get("final_nav"),
                "fixed_risk_fraction": run_metrics.get("fixed_risk_fraction"),
                "fee_profile": run_metrics.get("fee_profile"),
            }
        )
        windows.append(window_metrics)

    if len(windows) != 3:
        raise RuntimeError(f"expected three independent evaluation windows, found {len(windows)}")
    combined = pd.concat(audits, ignore_index=True, sort=False).sort_values(
        ["ts_opened_sort", "position_id"],
    )
    total_days = sum(int(window["calendar_days"]) for window in windows)
    aggregate = metrics(combined, total_days)
    aggregate["positive_windows"] = int(
        sum((window["mean_net_r"] or 0.0) > 0.0 for window in windows)
    )
    aggregate["risk_fraction"] = RISK_FRACTION
    aggregate["symbols"] = sorted(combined["symbol"].astype(str).unique())
    aggregate["one_account_one_position"] = True
    aggregate["partial_profit_taking"] = False
    aggregate["partial_stopping"] = False

    by_symbol = {
        str(symbol): metrics(group, total_days)
        for symbol, group in combined.groupby("symbol", sort=True)
    }
    result = {
        "candidate": "candidate-ml-easychart-c",
        "observation_kind": "THREE_UNTOUCHED_NON_OVERLAPPING_SHORT_WINDOWS_COMPOUNDED_IN_CHRONOLOGICAL_R_ORDER",
        "aggregate_final_holdout": aggregate,
        "final_holdout_periods": windows,
        "by_symbol": by_symbol,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
