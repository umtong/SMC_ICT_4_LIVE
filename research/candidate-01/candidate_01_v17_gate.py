#!/usr/bin/env python3
"""Verify and classify frozen candidate-01 v17 Nautilus weekly evidence."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

SUMMARY_NAME = "resolved_impact_v17_summary.json"
REQUIRED_FILES = (
    SUMMARY_NAME,
    "nautilus_metrics.json",
    "execution_contract.json",
    "daily_nav.csv",
    "orders.csv",
    "positions.csv",
    "account.csv",
    "trade_plans.csv",
    "execution_events.jsonl",
    "resolution_transitions.csv",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def expected_dates(week: str) -> list[str]:
    start = date.fromisoformat(week)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(7)]


def read_daily_dates(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return [str(row["date"]) for row in csv.DictReader(stream)]


def profit_factor_gate_value(metrics: dict[str, Any]) -> float:
    raw = metrics.get("profit_factor")
    if raw is not None:
        return float(raw)
    trades = int(metrics.get("closed_positions", 0))
    win_rate = float(metrics.get("win_rate") or 0.0)
    return 999.0 if trades and win_rate == 1.0 else 0.0


def classify(root: Path, week: str) -> dict[str, Any]:
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"missing authoritative evidence in {root}: {missing}")

    summary = read_json(root / SUMMARY_NAME)
    metrics = read_json(root / "nautilus_metrics.json")
    contract = read_json(root / "execution_contract.json")
    daily_dates = read_daily_dates(root / "daily_nav.csv")

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
    assert daily_dates == expected_dates(week), (daily_dates, expected_dates(week))

    trades = int(metrics["closed_positions"])
    win_rate = float(metrics.get("win_rate") or 0.0)
    total_return = float(metrics["total_return"])
    geometric_daily = float(metrics["geometric_mean_daily_return"])
    profit_factor = metrics.get("profit_factor")
    pf_value = profit_factor_gate_value(metrics)
    max_drawdown = float(metrics["max_drawdown"])
    operational = (
        bool(metrics["ended_flat"])
        and int(metrics["one_global_entry_gate_violations"]) == 0
        and int(metrics["protective_order_failures"]) == 0
        and int(metrics["liquidation_marker_rows"]) == 0
    )
    full_pass = (
        operational
        and trades >= 7
        and win_rate >= 0.45
        and total_return > 0.0
        and geometric_daily >= 0.01
        and pf_value >= 1.2
        and max_drawdown > -0.20
    )
    promising = (
        operational
        and not full_pass
        and trades >= 7
        and win_rate >= 0.45
        and total_return > 0.0
        and geometric_daily >= 0.0075
        and pf_value >= 1.1
        and max_drawdown > -0.15
    )
    classification = (
        "full_pass" if full_pass else "promising_but_not_complete" if promising else "stop"
    )
    gate = {
        "week": week,
        "rule": summary["rule"],
        "classification": classification,
        "advance": full_pass or promising,
        "operational_gate": operational,
        "initiative_plans_in_stream": int(summary["initiative_plans_in_stream"]),
        "selected_plan_count": int(summary["selected_plan_count"]),
        "selected_response_counts": summary["selected_response_counts"],
        "resolution_counts": summary["resolution_counts"],
        "trades": trades,
        "win_rate": win_rate,
        "total_return": total_return,
        "geometric_daily": geometric_daily,
        "profit_factor": profit_factor,
        "profit_factor_gate_value": pf_value,
        "max_drawdown": max_drawdown,
        "rejection_counts": metrics["rejection_counts"],
        "ended_flat": bool(metrics["ended_flat"]),
        "one_global_entry_gate_violations": int(
            metrics["one_global_entry_gate_violations"],
        ),
        "protective_order_failures": int(metrics["protective_order_failures"]),
        "liquidation_marker_rows": int(metrics["liquidation_marker_rows"]),
        "daily_nav_dates": daily_dates,
    }
    (root / "week_gate.json").write_text(
        json.dumps(gate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(gate, indent=2, sort_keys=True))
    return gate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--week", required=True)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    classify(args.root, args.week)
