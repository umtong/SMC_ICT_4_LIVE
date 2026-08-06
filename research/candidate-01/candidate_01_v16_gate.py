#!/usr/bin/env python3
"""Verify and classify frozen candidate-01 v16 Nautilus weekly evidence."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

SUMMARY_NAME = "intrinsic_hierarchical_auction_v16_summary.json"
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
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def profit_factor_value(metrics: dict[str, Any]) -> float:
    raw = metrics.get("profit_factor")
    if raw is not None:
        return float(raw)
    trades = int(metrics.get("closed_positions", 0))
    win_rate = float(metrics.get("win_rate") or 0.0)
    return 999.0 if trades > 0 and win_rate == 1.0 else 0.0


def expected_dates(week: str) -> list[str]:
    start = date.fromisoformat(week)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(7)]


def read_daily_dates(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    return [str(row["date"]) for row in rows]


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
    pf_value = profit_factor_value(metrics)
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
        "plans": int(summary["submitted_plan_pool"]),
        "hierarchical_reversal_plans": int(summary["hierarchical_reversal_plans"]),
        "failed_reversal_measured_plans": int(summary["failed_reversal_measured_plans"]),
        "direct_measured_plans": int(summary["direct_measured_plans"]),
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


def select_rule(primary_root: Path, control_root: Path, output: Path) -> dict[str, Any]:
    primary = read_json(primary_root / "week_gate.json")
    control = read_json(control_root / "week_gate.json")
    if primary["advance"]:
        selected_rule = str(primary["rule"])
        selected_classification = str(primary["classification"])
        reason = "PRIMARY_ADVANCED"
    elif control["advance"]:
        selected_rule = str(control["rule"])
        selected_classification = str(control["classification"])
        reason = "PRIMARY_STOPPED_CONTROL_ADVANCED"
    else:
        selected_rule = "none"
        selected_classification = "stop"
        reason = "BOTH_RULES_STOPPED"
    selection = {
        "selected_rule": selected_rule,
        "classification": selected_classification,
        "advance": selected_rule != "none",
        "selection_reason": reason,
        "primary": primary,
        "control": control,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(selection, indent=2, sort_keys=True))
    return selection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    classify_parser = subparsers.add_parser("classify")
    classify_parser.add_argument("--root", type=Path, required=True)
    classify_parser.add_argument("--week", required=True)
    select_parser = subparsers.add_parser("select")
    select_parser.add_argument("--primary-root", type=Path, required=True)
    select_parser.add_argument("--control-root", type=Path, required=True)
    select_parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "classify":
        classify(args.root, args.week)
    else:
        select_rule(args.primary_root, args.control_root, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
