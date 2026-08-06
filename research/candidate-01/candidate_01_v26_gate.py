#!/usr/bin/env python3
"""Strict weekly gate for candidate-01 v26 Nautilus evidence."""
from __future__ import annotations
import argparse, csv, json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SUMMARY = "funding_window_auction_resolver_v26_summary.json"
REQUIRED = (
    SUMMARY, "nautilus_metrics.json", "execution_contract.json", "daily_nav.csv",
    "orders.csv", "positions.csv", "account.csv", "trade_plans.csv",
    "rejections.csv", "execution_events.jsonl", "auction_transitions.csv",
    "completed_funding_windows.csv", "scenario_plans.csv",
    "daily_clock_calibrations.json",
)

def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def expected(week: str) -> list[str]:
    start = date.fromisoformat(week)
    return [(start + timedelta(days=i)).isoformat() for i in range(7)]

def pf_value(metrics: dict[str, Any]) -> float:
    raw = metrics.get("profit_factor")
    if raw is not None:
        return float(raw)
    return 999.0 if int(metrics.get("closed_positions", 0)) > 0 and float(metrics.get("win_rate") or 0.0) == 1.0 else 0.0

def classify(root: Path, week: str) -> dict[str, Any]:
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(missing)
    summary = load(root / SUMMARY)
    metrics = load(root / "nautilus_metrics.json")
    contract = load(root / "execution_contract.json")
    with (root / "daily_nav.csv").open(encoding="utf-8", newline="") as stream:
        nav_dates = [row["date"] for row in csv.DictReader(stream)]
    assert summary["authoritative_backtest"] is True
    assert summary["execution_engine"] == "NautilusTrader"
    assert summary["execution_data_type"] == "TradeTick"
    assert summary["custom_fill_simulator"] is False
    assert summary["custom_pnl_or_nav_ledger"] is False
    assert int(summary["context_days"]) == 7
    assert int(summary["funding_window_hours"]) == 8
    assert int(summary["response_events"]) == 2
    assert float(summary["round_trip_cost_bps"]) == 14.0
    assert float(summary["risk_fraction"]) == 0.03
    assert float(summary["all_in_cost_bps_per_side"]) == 7.0
    assert metrics["execution_engine"] == "NautilusTrader"
    assert metrics["trade_execution"] is True
    assert metrics["custom_fill_simulator"] is False
    assert metrics["custom_pnl_or_nav_ledger"] is False
    assert contract["authoritative_performance_engine"] == "NautilusTrader"
    assert nav_dates == expected(week), (nav_dates, expected(week))
    trades = int(metrics["closed_positions"])
    win_rate = float(metrics.get("win_rate") or 0.0)
    total_return = float(metrics["total_return"])
    geo = float(metrics["geometric_mean_daily_return"])
    pf = metrics.get("profit_factor")
    pf_gate = pf_value(metrics)
    mdd = float(metrics["max_drawdown"])
    operational = bool(metrics["ended_flat"]) and int(metrics["one_global_entry_gate_violations"]) == 0 and int(metrics["protective_order_failures"]) == 0 and int(metrics["liquidation_marker_rows"]) == 0
    full = operational and trades >= 7 and win_rate >= 0.45 and total_return > 0.0 and geo >= 0.01 and pf_gate >= 1.20 and mdd > -0.20
    result = {
        "week": week, "rule": summary["rule"],
        "classification": "full_pass" if full else "stop", "advance": full,
        "operational_gate": operational, "state_counts": summary["state_counts"],
        "selected_plan_count": int(summary["selected_plan_count"]),
        "completed_funding_window_count": int(summary["completed_funding_window_count"]),
        "trades": trades, "win_rate": win_rate, "total_return": total_return,
        "geometric_daily": geo, "profit_factor": pf,
        "profit_factor_gate_value": pf_gate, "max_drawdown": mdd,
        "rejection_counts": metrics["rejection_counts"],
        "ended_flat": bool(metrics["ended_flat"]), "daily_nav_dates": nav_dates,
    }
    (root / "week_gate.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--week", required=True)
    args = parser.parse_args()
    classify(args.root, args.week)
