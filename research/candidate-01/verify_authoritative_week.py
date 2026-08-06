#!/usr/bin/env python3
"""Dependency-free verifier for one authoritative candidate-01 week.

This verifier never calculates fills, PnL or NAV.  It validates the execution
contract, reads NautilusTrader-produced metrics and daily NAV, classifies the
predeclared short-week gate, and writes ``week_gate.json``.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def expected_dates(start: str, days: int = 7) -> list[str]:
    first = date.fromisoformat(start)
    return [(first + timedelta(days=index)).isoformat() for index in range(days)]


def read_daily_dates(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return [str(row["date"]) for row in csv.DictReader(stream)]


def profit_factor_value(metrics: dict[str, Any]) -> float:
    value = metrics.get("profit_factor")
    if value is not None:
        return float(value)
    trades = int(metrics.get("closed_positions", 0))
    win_rate = float(metrics.get("win_rate") or 0.0)
    return 999.0 if trades > 0 and win_rate == 1.0 else 0.0


def run(args: argparse.Namespace) -> int:
    root = args.output
    summary = read_json(root / args.summary)
    metrics = read_json(root / "nautilus_metrics.json")
    contract = read_json(root / "execution_contract.json")

    assert summary["authoritative_backtest"] is True
    assert summary["execution_engine"] == "NautilusTrader"
    assert summary["custom_fill_simulator"] is False
    assert summary["custom_pnl_or_nav_ledger"] is False
    assert abs(float(summary["risk_fraction"]) - 0.03) < 1e-12
    assert metrics["execution_engine"] == "NautilusTrader"
    assert metrics["custom_fill_simulator"] is False
    assert metrics["custom_pnl_or_nav_ledger"] is False
    assert metrics["trade_execution"] is True
    assert contract["authoritative_performance_engine"] == "NautilusTrader"
    assert contract["custom_fill_simulator"] is False
    assert contract["custom_pnl_or_nav_ledger"] is False
    assert contract["trade_execution"] is True

    required = (
        "orders.csv",
        "positions.csv",
        "account.csv",
        "daily_nav.csv",
        "trade_plans.csv",
        "rejections.csv",
        "execution_events.jsonl",
    )
    missing = [name for name in required if not (root / name).is_file()]
    assert not missing, f"missing authoritative evidence: {missing}"

    dates = read_daily_dates(root / "daily_nav.csv")
    expected = expected_dates(args.week)
    assert dates == expected, f"daily NAV dates {dates} != {expected}"

    trades = int(metrics["closed_positions"])
    geo = float(metrics["geometric_mean_daily_return"])
    total = float(metrics["total_return"])
    win = float(metrics.get("win_rate") or 0.0)
    pf_raw = metrics.get("profit_factor")
    pf = profit_factor_value(metrics)
    mdd = float(metrics["max_drawdown"])
    full_pass = bool(
        trades >= args.full_min_trades
        and geo >= args.full_min_geo
        and total > 0.0
        and win >= args.full_min_win
        and pf > 1.0
        and mdd > args.full_max_drawdown
    )
    near_target = bool(
        not full_pass
        and trades >= args.near_min_trades
        and geo >= args.near_min_geo
        and total > 0.0
        and pf >= 1.0
        and mdd > args.near_max_drawdown
    )
    classification = (
        "full_pass" if full_pass else "near_target" if near_target else "stop"
    )
    gate = {
        "week": args.week,
        "classification": classification,
        "advance": full_pass or near_target,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": summary.get("execution_data_type"),
        "risk_fraction": metrics["risk_fraction"],
        "all_in_cost_bps_per_side": metrics["all_in_cost_bps_per_side"],
        "submitted_plan_pool": summary.get("submitted_plan_pool"),
        "trades": trades,
        "win_rate": metrics.get("win_rate"),
        "total_return": total,
        "geometric_daily": geo,
        "profit_factor": pf_raw,
        "max_drawdown": mdd,
        "target_met": metrics["target_met"],
        "rejection_counts": metrics["rejection_counts"],
        "ended_flat": metrics["ended_flat"],
        "one_global_entry_gate_violations": metrics[
            "one_global_entry_gate_violations"
        ],
        "protective_order_failures": metrics["protective_order_failures"],
        "liquidation_marker_rows": metrics["liquidation_marker_rows"],
        "daily_nav_dates": dates,
    }
    (root / "week_gate.json").write_text(
        json.dumps(gate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(gate, indent=2, sort_keys=True))
    return 0 if gate["advance"] else 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--summary", required=True)
    result.add_argument("--week", required=True)
    result.add_argument("--full-min-trades", type=int, default=7)
    result.add_argument("--full-min-geo", type=float, default=0.01)
    result.add_argument("--full-min-win", type=float, default=0.40)
    result.add_argument("--full-max-drawdown", type=float, default=-0.20)
    result.add_argument("--near-min-trades", type=int, default=5)
    result.add_argument("--near-min-geo", type=float, default=0.005)
    result.add_argument("--near-max-drawdown", type=float, default=-0.15)
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
