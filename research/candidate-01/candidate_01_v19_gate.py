#!/usr/bin/env python3
"""Verify and classify frozen candidate-01 v19 Nautilus weekly evidence."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

SUMMARY = "boundary_retest_resolved_impact_v19_summary.json"
REQUIRED = (
    SUMMARY, "nautilus_metrics.json", "execution_contract.json",
    "daily_nav.csv", "orders.csv", "positions.csv", "account.csv",
    "trade_plans.csv", "execution_events.jsonl", "resolution_transitions.csv",
    "entry_instructions.csv",
)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def dates(week: str) -> list[str]:
    start = date.fromisoformat(week)
    return [(start + timedelta(days=i)).isoformat() for i in range(7)]


def pf_value(metrics: dict[str, Any]) -> float:
    raw = metrics.get("profit_factor")
    if raw is not None:
        return float(raw)
    return 999.0 if int(metrics.get("closed_positions", 0)) and float(metrics.get("win_rate") or 0.0) == 1.0 else 0.0


def classify(root: Path, week: str) -> dict[str, Any]:
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"missing authoritative evidence: {missing}")
    summary = load(root / SUMMARY)
    metrics = load(root / "nautilus_metrics.json")
    contract = load(root / "execution_contract.json")
    with (root / "daily_nav.csv").open(encoding="utf-8", newline="") as stream:
        daily_dates = [row["date"] for row in csv.DictReader(stream)]

    assert summary["authoritative_backtest"] is True
    assert summary["execution_engine"] == "NautilusTrader"
    assert summary["execution_data_type"] == "TradeTick"
    assert summary["custom_fill_simulator"] is False
    assert summary["custom_pnl_or_nav_ledger"] is False
    assert float(summary["risk_fraction"]) == 0.03
    assert int(summary["clock_minutes"]) == 20
    assert int(summary["context_days"]) == 3
    assert metrics["execution_engine"] == "NautilusTrader"
    assert metrics["trade_execution"] is True
    assert metrics["custom_fill_simulator"] is False
    assert metrics["custom_pnl_or_nav_ledger"] is False
    assert float(metrics["risk_fraction"]) == 0.03
    assert contract["authoritative_performance_engine"] == "NautilusTrader"
    assert daily_dates == dates(week), (daily_dates, dates(week))

    trades = int(metrics["closed_positions"])
    win = float(metrics.get("win_rate") or 0.0)
    total = float(metrics["total_return"])
    geo = float(metrics["geometric_mean_daily_return"])
    pf = metrics.get("profit_factor")
    pfv = pf_value(metrics)
    mdd = float(metrics["max_drawdown"])
    limit_clean = bool(metrics.get("ended_without_pending_entry", True))
    operational = (
        bool(metrics["ended_flat"]) and limit_clean
        and int(metrics["one_global_entry_gate_violations"]) == 0
        and int(metrics["protective_order_failures"]) == 0
        and int(metrics["liquidation_marker_rows"]) == 0
    )
    full = operational and trades >= 7 and win >= 0.45 and total > 0 and geo >= 0.01 and pfv >= 1.2 and mdd > -0.20
    promising = operational and not full and trades >= 7 and win >= 0.45 and total > 0 and geo >= 0.0075 and pfv >= 1.1 and mdd > -0.15
    classification = "full_pass" if full else "promising_but_not_complete" if promising else "stop"
    gate = {
        "week": week,
        "rule": summary["rule"],
        "classification": classification,
        "advance": full or promising,
        "operational_gate": operational,
        "resolved_plan_count": int(summary["resolved_plan_count"]),
        "entry_instruction_count": int(summary["entry_instruction_count"]),
        "response_counts": summary["response_counts"],
        "limit_entries_expired": int(summary["limit_entries_expired"]),
        "targets_consumed_before_entry": int(summary["targets_consumed_before_entry"]),
        "trades": trades,
        "win_rate": win,
        "total_return": total,
        "geometric_daily": geo,
        "profit_factor": pf,
        "profit_factor_gate_value": pfv,
        "max_drawdown": mdd,
        "rejection_counts": metrics["rejection_counts"],
        "ended_flat": bool(metrics["ended_flat"]),
        "ended_without_pending_entry": limit_clean,
        "daily_nav_dates": daily_dates,
    }
    (root / "week_gate.json").write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(gate, indent=2, sort_keys=True))
    return gate


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--week", required=True)
    args = parser.parse_args()
    classify(args.root, args.week)
