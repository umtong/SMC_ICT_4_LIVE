#!/usr/bin/env python3
"""Verify one candidate week from NautilusTrader-owned evidence."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError


def realized_pnl(value: Any) -> float:
    return float(str(value).split()[0].replace("_", "").replace(",", ""))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--min-trades", type=int, default=7)
    parser.add_argument("--min-active-days", type=int, default=4)
    parser.add_argument("--min-win-rate", type=float, default=0.55)
    parser.add_argument("--min-geometric-daily", type=float, default=0.01)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    metrics = json.loads((args.root / "nautilus/metrics.json").read_text())
    signals = json.loads((args.root / "signals/summary.json").read_text())
    events = json.loads((args.root / "nautilus/strategy_events.json").read_text())
    try:
        positions = pd.read_csv(args.root / "nautilus/positions.csv")
    except EmptyDataError:
        positions = pd.DataFrame()

    entries = [
        event for event in events if event.get("event_type") == "ENTRY_SUBMITTED"
    ]
    if len(entries) != len(positions.index):
        raise SystemExit(
            f"entry-position mismatch entries={len(entries)} "
            f"positions={len(positions.index)}"
        )

    loss_fractions: list[float] = []
    for position, entry in zip(positions.to_dict("records"), entries):
        pnl = realized_pnl(position["realized_pnl"])
        equity = float(entry["details"]["equity"])
        if not math.isfinite(equity) or equity <= 0.0:
            raise SystemExit("invalid entry NAV")
        if pnl < 0.0:
            loss_fractions.append(abs(pnl) / equity)
    maximum_loss = max(loss_fractions, default=0.0)
    risk_pass = maximum_loss <= 0.0301

    trades = int(metrics.get("trades") or 0)
    active_days = int(metrics.get("active_days") or 0)
    win_rate = float(metrics.get("win_rate") or 0.0)
    geometric_daily = float(metrics.get("geometric_daily_growth") or 0.0)
    total_return = float(metrics.get("total_return") or 0.0)
    alpha_checks = {
        "positive_post_cost_nav": total_return > 0.0,
        "geometric_daily": geometric_daily >= args.min_geometric_daily,
        "trades": trades >= args.min_trades,
        "active_days": active_days >= args.min_active_days,
        "win_rate": win_rate >= args.min_win_rate,
    }
    candidate_pass = risk_pass and all(alpha_checks.values())

    summary = {
        "candidate": args.candidate,
        "stage": args.stage,
        "engine": metrics.get("engine"),
        "signals": signals.get("written_signals"),
        "route_counts": signals.get("route_counts"),
        "trades": trades,
        "wins": metrics.get("wins"),
        "win_rate": win_rate,
        "total_return": total_return,
        "geometric_daily_growth": geometric_daily,
        "active_days": active_days,
        "max_drawdown": metrics.get("max_drawdown"),
        "largest_winner_share": metrics.get("largest_winner_share"),
        "scenario_metrics": metrics.get("scenario_metrics"),
        "gate_checks": metrics.get("gate_checks"),
        "maximum_realized_loss_fraction": maximum_loss,
        "risk_pass": risk_pass,
        "alpha_checks": alpha_checks,
        "candidate_pass": candidate_pass,
    }
    output = args.output or args.root / "summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not risk_pass:
        raise SystemExit("realized loss exceeded 3% current-NAV contract")


if __name__ == "__main__":
    main()
