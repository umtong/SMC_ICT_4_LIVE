#!/usr/bin/env python3
"""Strict weekly gate for candidate-01 v34 Nautilus evidence."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

SUMMARY = "impact_saturation_reversal_v34_summary.json"
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
    "initiative_impact_profiles.csv",
    "impact_saturation_reversal_decisions.csv",
    "initiative_events.csv",
    "scenario_transitions.csv",
    "scenario_plans.csv",
    "primary_plans.csv",
    "control_plans.csv",
    "daily_clock_calibrations.json",
)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


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
    nav_rows = read_csv(root / "daily_nav.csv")
    primary_rows = read_csv(root / "primary_plans.csv")
    control_rows = read_csv(root / "control_plans.csv")
    profile_rows = read_csv(root / "initiative_impact_profiles.csv")
    decision_rows = read_csv(root / "impact_saturation_reversal_decisions.csv")
    nav_dates = [row["date"] for row in nav_rows]

    assert summary["authoritative_backtest"] is True
    assert summary["execution_engine"] == "NautilusTrader"
    assert summary["execution_data_type"] == "TradeTick"
    assert summary["custom_fill_simulator"] is False
    assert summary["custom_pnl_or_nav_ledger"] is False
    assert int(summary["candidate_version"]) == 34
    assert summary["rule"] in (
        "impact-saturation-reversal",
        "immediate-reversal-control",
    )
    assert int(summary["context_days"]) == 4
    assert int(summary["structure_bars"]) == 20
    assert int(summary["pulse_bars"]) == 3
    assert abs(float(summary["cost_resolved_move_bps"]) - 40.0) < 1e-9
    assert int(summary["primary_plan_count"]) <= int(
        summary["control_plan_count"],
    )
    expected_selected = (
        int(summary["primary_plan_count"])
        if summary["rule"] == "impact-saturation-reversal"
        else int(summary["control_plan_count"])
    )
    assert int(summary["selected_plan_count"]) == expected_selected
    assert len(primary_rows) == int(summary["primary_plan_count"])
    assert len(control_rows) == int(summary["control_plan_count"])
    assert all(
        ":impact-saturation-reversal:" in row["scenario_id"]
        for row in primary_rows
    )
    assert all(
        ":immediate-reversal-control:" in row["scenario_id"]
        for row in control_rows
    )
    assert len(profile_rows) == int(summary["initiative_impact_profile_count"])
    assert len(decision_rows) == int(summary["accepted_pullback_decision_count"])
    assert int(summary["saturated_initiative_profile_count"]) <= len(profile_rows)
    assert int(summary["impact_saturation_confirmed_decision_count"]) <= len(
        decision_rows,
    )
    assert int(summary["counterflow_dominant_decision_count"]) <= len(
        decision_rows,
    )
    assert float(summary["risk_fraction"]) == 0.03
    assert float(summary["all_in_cost_bps_per_side"]) == 7.0

    assert metrics["execution_engine"] == "NautilusTrader"
    assert metrics["trade_execution"] is True
    assert metrics["custom_fill_simulator"] is False
    assert metrics["custom_pnl_or_nav_ledger"] is False
    assert float(metrics["risk_fraction"]) == 0.03
    assert contract["authoritative_performance_engine"] == "NautilusTrader"
    assert contract["custom_fill_simulator"] is False
    assert contract["custom_pnl_or_nav_ledger"] is False
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
        "candidate_version": 34,
        "rule": summary["rule"],
        "classification": classification,
        "advance": full_pass or promising,
        "operational_gate": operational,
        "scenario_counts": summary["scenario_counts"],
        "selected_plan_count": int(summary["selected_plan_count"]),
        "primary_plan_count": int(summary["primary_plan_count"]),
        "control_plan_count": int(summary["control_plan_count"]),
        "initiative_impact_profile_count": int(
            summary["initiative_impact_profile_count"],
        ),
        "saturated_initiative_profile_count": int(
            summary["saturated_initiative_profile_count"],
        ),
        "accepted_pullback_decision_count": int(
            summary["accepted_pullback_decision_count"],
        ),
        "impact_saturation_confirmed_decision_count": int(
            summary["impact_saturation_confirmed_decision_count"],
        ),
        "counterflow_dominant_decision_count": int(
            summary["counterflow_dominant_decision_count"],
        ),
        "profile_reason_counts": summary["profile_reason_counts"],
        "decision_reason_counts": summary["decision_reason_counts"],
        "trades": trades,
        "win_rate": win_rate,
        "total_return": total_return,
        "geometric_daily": geometric_daily,
        "profit_factor": profit_factor,
        "profit_factor_gate_value": profit_factor_gate,
        "max_drawdown": max_drawdown,
        "rejection_counts": metrics["rejection_counts"],
        "ended_flat": bool(metrics["ended_flat"]),
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
    arguments = parser.parse_args()
    classify(arguments.root, arguments.week)
