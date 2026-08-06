#!/usr/bin/env python3
"""Strict weekly gate for candidate-01 v31 Nautilus evidence."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

SUMMARY = "failed_acceptance_reversal_v31_summary.json"
REQUIRED = (
    SUMMARY,
    "nautilus_metrics.json",
    "execution_contract.json",
    "daily_nav.csv",
    "orders.csv",
    "positions.csv",
    "account.csv",
    "trade_plans.csv",
    "rejections.csv",
    "execution_events.jsonl",
    "initiative_events.csv",
    "scenario_transitions.csv",
    "scenario_plans.csv",
    "primary_plans.csv",
    "control_plans.csv",
    "stop_entry_instructions.csv",
    "entry_decisions.csv",
    "daily_clock_calibrations.json",
)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def expected_dates(week: str) -> list[str]:
    start = date.fromisoformat(week)
    return [(start + timedelta(days=index)).isoformat() for index in range(7)]


def profit_factor_value(metrics: dict[str, Any]) -> float:
    raw = metrics.get("profit_factor")
    if raw is not None:
        return float(raw)
    trades = int(metrics.get("closed_positions", 0))
    win_rate = float(metrics.get("win_rate") or 0.0)
    return 999.0 if trades > 0 and win_rate == 1.0 else 0.0


def classify(root: Path, week: str) -> dict[str, Any]:
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"missing authoritative evidence: {missing}")
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
    assert int(summary["candidate_version"]) == 31
    assert summary["rule"] in (
        "boundary-loss-reversal",
        "immediate-reversal-control",
    )
    assert int(summary["context_days"]) == 4
    assert int(summary["structure_bars"]) == 20
    assert int(summary["pulse_bars"]) == 3
    assert abs(float(summary["cost_resolved_move_bps"]) - 40.0) < 1e-9
    assert abs(float(summary["btc_tick_size"]) - 0.1) < 1e-12
    assert abs(float(summary["stop_limit_protection_bps"]) - 7.0) < 1e-9
    assert int(summary["selected_plan_count"]) == int(
        summary["selected_instruction_count"],
    )
    assert float(summary["risk_fraction"]) == 0.03
    assert float(summary["all_in_cost_bps_per_side"]) == 7.0
    assert metrics["execution_engine"] == "NautilusTrader"
    assert metrics["trade_execution"] is True
    assert metrics["custom_fill_simulator"] is False
    assert metrics["custom_pnl_or_nav_ledger"] is False
    assert float(metrics["risk_fraction"]) == 0.03
    assert contract["authoritative_performance_engine"] == "NautilusTrader"
    if summary["rule"] == "boundary-loss-reversal":
        assert summary["entry_order_type"] == "STOP_LIMIT"
        assert metrics["entry_order_type"] == "STOP_LIMIT"
        assert contract["entry_order_type"] == "STOP_LIMIT"
        assert contract["risk_sized_at_worst_permitted_limit_price"] is True
        assert contract["pending_invalidation_and_target_first_cancel"] is True
    else:
        assert summary["entry_order_type"] == "MARKET"
    assert nav_dates == expected_dates(week), (nav_dates, expected_dates(week))

    trades = int(metrics["closed_positions"])
    win_rate = float(metrics.get("win_rate") or 0.0)
    total_return = float(metrics["total_return"])
    geometric_daily = float(metrics["geometric_mean_daily_return"])
    profit_factor = metrics.get("profit_factor")
    profit_factor_gate = profit_factor_value(metrics)
    max_drawdown = float(metrics["max_drawdown"])
    operational = (
        bool(metrics["ended_flat"])
        and bool(metrics.get("ended_without_pending_entry", True))
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
        and profit_factor_gate >= 1.20
        and max_drawdown > -0.20
    )
    promising = (
        operational
        and not full_pass
        and trades >= 7
        and win_rate >= 0.45
        and total_return > 0.0
        and geometric_daily >= 0.0075
        and profit_factor_gate >= 1.10
        and max_drawdown > -0.15
    )
    classification = (
        "full_pass"
        if full_pass
        else "promising_but_not_complete"
        if promising
        else "stop"
    )
    gate = {
        "week": week,
        "candidate_version": 31,
        "rule": summary["rule"],
        "classification": classification,
        "advance": full_pass or promising,
        "operational_gate": operational,
        "state_counts": summary["state_counts"],
        "initiative_event_count": int(summary["initiative_event_count"]),
        "selected_plan_count": int(summary["selected_plan_count"]),
        "selected_instruction_count": int(summary["selected_instruction_count"]),
        "entry_order_type": summary["entry_order_type"],
        "trades": trades,
        "win_rate": win_rate,
        "total_return": total_return,
        "geometric_daily": geometric_daily,
        "profit_factor": profit_factor,
        "profit_factor_gate_value": profit_factor_gate,
        "max_drawdown": max_drawdown,
        "rejection_counts": metrics["rejection_counts"],
        "stop_entries_expired": int(metrics.get("stop_entries_expired", 0)),
        "targets_consumed_before_entry": int(
            metrics.get("targets_consumed_before_entry", 0),
        ),
        "invalidations_before_entry": int(
            metrics.get("invalidations_before_entry", 0),
        ),
        "ended_flat": bool(metrics["ended_flat"]),
        "ended_without_pending_entry": bool(
            metrics.get("ended_without_pending_entry", True),
        ),
        "daily_nav_dates": nav_dates,
    }
    (root / "week_gate.json").write_text(
        json.dumps(gate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(gate, indent=2, sort_keys=True))
    return gate


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--week", required=True)
    args = parser.parse_args()
    classify(args.root, args.week)
