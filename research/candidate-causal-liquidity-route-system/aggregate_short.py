#!/usr/bin/env python3
"""Aggregate disjoint short diagnostics without training or promotion gates."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

RISK_FRACTION = 0.03
EPS = 1e-12


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "average_net_r": 0.0,
            "average_win_r": 0.0,
            "average_loss_r": 0.0,
            "payoff_ratio": 0.0,
            "profit_factor": 0.0,
            "nav_final": 1.0,
            "max_drawdown": 0.0,
            "mean_gross_rr": 0.0,
            "median_holding_minutes": 0.0,
        }
    values = pd.to_numeric(frame.net_r, errors="coerce").dropna()
    wins, losses = values[values > 0.0], values[values < 0.0]
    nav, peak, max_drawdown = 1.0, 1.0, 0.0
    for value in values:
        nav *= max(EPS, 1.0 + RISK_FRACTION * float(value))
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1.0)
    return {
        "trades": int(len(values)),
        "wins": int((values > 0.0).sum()),
        "losses": int((values < 0.0).sum()),
        "win_rate": float((values > 0.0).mean()) if len(values) else 0.0,
        "average_net_r": float(values.mean()) if len(values) else 0.0,
        "average_win_r": float(wins.mean()) if len(wins) else 0.0,
        "average_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "payoff_ratio": float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else 0.0,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(wins) and len(losses) else (float("inf") if len(wins) else 0.0),
        "nav_final": float(nav),
        "max_drawdown": float(max_drawdown),
        "mean_gross_rr": float(pd.to_numeric(frame.gross_rr, errors="coerce").mean()),
        "median_holding_minutes": float(pd.to_numeric(frame.holding_minutes, errors="coerce").median()),
    }


def _group_metrics(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty or column not in frame:
        return {}
    return {str(name): _metrics(group) for name, group in frame.groupby(column)}


def _read_frames(root: Path, name: str) -> pd.DataFrame:
    frames = []
    for path in root.rglob(name):
        try:
            frame = pd.read_csv(path)
        except Exception:
            continue
        summary_path = path.parent / "summary.json"
        role = "unknown"
        period = path.parent.name
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text())
                role = str(summary.get("role", role))
                period = f"{summary.get('start', '')}_{summary.get('end', '')}"
            except Exception:
                pass
        frame["diagnostic_role"] = role
        frame["diagnostic_period"] = period
        frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _collect_summaries(root: Path) -> list[dict[str, Any]]:
    output = []
    for path in root.rglob("summary.json"):
        try:
            value = json.loads(path.read_text())
        except Exception:
            continue
        if "policy" in value and str(value["policy"]).startswith("CAUSAL_LIQUIDITY_ROUTE"):
            output.append(value)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    summaries = _collect_summaries(args.root)
    trades = _read_frames(args.root, "global_trades.csv")
    plans = _read_frames(args.root, "candidate_plans.csv.gz")
    diagnostics = _read_frames(args.root, "source_event_diagnostics.csv.gz")
    account_skipped = _read_frames(args.root, "account_skipped.csv")
    if not trades.empty:
        trades = trades.sort_values(["entry_time_ns", "diagnostic_period", "episode_id"]).reset_index(drop=True)
    role_metrics = {
        role: _metrics(group)
        for role, group in trades.groupby("diagnostic_role")
    } if not trades.empty else {}
    all_metrics = _metrics(trades)
    calendar_days_by_role: dict[str, int] = {}
    for summary in summaries:
        role = str(summary.get("role", "unknown"))
        calendar_days_by_role[role] = calendar_days_by_role.get(role, 0) + int(summary.get("account", {}).get("calendar_days", 0))
    for role, metrics in role_metrics.items():
        days = max(calendar_days_by_role.get(role, 0), 1)
        metrics["calendar_days"] = int(days)
        metrics["trades_per_calendar_day"] = float(metrics["trades"] / days)
    all_days = max(sum(calendar_days_by_role.values()), 1)
    all_metrics["calendar_days"] = int(all_days)
    all_metrics["trades_per_calendar_day"] = float(all_metrics["trades"] / all_days)

    result = {
        "policy": "CAUSAL_LIQUIDITY_ROUTE_SHORT_DIAGNOSTIC_AGGREGATE",
        "windows": int(len(summaries)),
        "candidate_plans": int(len(plans)),
        "source_events": int(len(diagnostics)),
        "account_skipped": int(len(account_skipped)),
        "all": all_metrics,
        "by_role": role_metrics,
        "by_symbol": _group_metrics(trades, "symbol"),
        "by_family": _group_metrics(trades, "family"),
        "by_source_kind": _group_metrics(trades, "source_kind"),
        "no_trade_reasons": diagnostics.reason.value_counts(dropna=False).to_dict() if not diagnostics.empty and "reason" in diagnostics else {},
        "window_summaries": summaries,
    }
    (args.output / "summary.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    trades.to_csv(args.output / "all_global_trades.csv", index=False)
    plans.to_csv(args.output / "all_candidate_plans.csv.gz", index=False, compression="gzip")
    diagnostics.to_csv(args.output / "all_source_event_diagnostics.csv.gz", index=False, compression="gzip")
    account_skipped.to_csv(args.output / "all_account_skipped.csv", index=False)
    if not trades.empty:
        trades[trades.net_r < 0.0].sort_values(["diagnostic_role", "net_r", "arbitration_score"]).to_csv(args.output / "loss_clinic.csv", index=False)
        trades[trades.net_r > 0.0].sort_values(["diagnostic_role", "arbitration_score"], ascending=[True, False]).to_csv(args.output / "win_clinic.csv", index=False)
    if not diagnostics.empty:
        no_trade = diagnostics[diagnostics.reason != "TRADE_PLAN"].copy() if "reason" in diagnostics else diagnostics.copy()
        if "counterfactual_mfe_r" in no_trade:
            no_trade = no_trade.sort_values(["diagnostic_role", "counterfactual_mfe_r"], ascending=[True, False])
        no_trade.to_csv(args.output / "no_trade_clinic.csv", index=False)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
