#!/usr/bin/env python3
"""Strict weekly gate for candidate-01 v35 authoritative evidence."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

SUMMARY = "positioning_cycle_mss_v35_summary.json"
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
    "directional_change_events.csv",
    "scenario_transitions.csv",
    "source_v23_plans.csv",
    "primary_plans.csv",
    "control_plans.csv",
    "scenario_plans.csv",
    "positioning_cycle_diagnostics.csv",
    "positioning_metrics.csv",
    "positioning_metric_downloads.json",
    "calendar_liquidity_levels.csv",
    "calendar_liquidity_events.csv",
    "calendar_target_selections.csv",
    "mss_displacement_diagnostics.csv",
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


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() in {"true", "1", "t", "yes"}


def classify(root: Path, week: str) -> dict[str, Any]:
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"missing authoritative evidence: {missing}")

    summary = load(root / SUMMARY)
    metrics = load(root / "nautilus_metrics.json")
    contract = load(root / "execution_contract.json")
    downloads = load(root / "positioning_metric_downloads.json")["downloads"]
    nav_rows = read_csv(root / "daily_nav.csv")
    source_rows = read_csv(root / "source_v23_plans.csv")
    primary_rows = read_csv(root / "primary_plans.csv")
    control_rows = read_csv(root / "control_plans.csv")
    selected_rows = read_csv(root / "scenario_plans.csv")
    diagnostic_rows = read_csv(root / "positioning_cycle_diagnostics.csv")
    metric_rows = read_csv(root / "positioning_metrics.csv")
    nav_dates = [row["date"] for row in nav_rows]

    assert summary["authoritative_backtest"] is True
    assert summary["execution_engine"] == "NautilusTrader"
    assert summary["execution_data_type"] == "TradeTick"
    assert summary["custom_fill_simulator"] is False
    assert summary["custom_pnl_or_nav_ledger"] is False
    assert int(summary["candidate_version"]) == 35
    assert summary["rule"] in (
        "oi-build-release-primary",
        "v23-mss-control",
    )
    assert int(summary["context_days"]) == 8
    assert int(summary["clock_minutes"]) == 1
    assert float(summary["positioning_metric_interval_minutes"]) == 5.0
    assert float(summary["positioning_metric_causal_delay_minutes"]) == 5.0
    assert summary["positioning_metric_checksums_match"] is True
    assert float(summary["risk_fraction"]) == 0.03
    assert float(summary["all_in_cost_bps_per_side"]) == 7.0

    source_count = int(summary["source_v23_plan_count"])
    primary_count = int(summary["primary_plan_count"])
    control_count = int(summary["control_plan_count"])
    confirmed_count = int(summary["positioning_cycle_confirmed_count"])
    selected_count = int(summary["selected_plan_count"])
    assert control_count == source_count
    assert primary_count <= control_count
    assert confirmed_count == primary_count
    assert len(source_rows) == source_count
    assert len(primary_rows) == primary_count
    assert len(control_rows) == control_count
    assert len(diagnostic_rows) == source_count
    assert len(selected_rows) == selected_count
    assert len(metric_rows) == int(summary["positioning_metric_observations"])
    assert len(metric_rows) > 0
    assert len(downloads) == int(summary["positioning_metric_download_count"])
    assert downloads
    assert all(row["sha256"] == row["expected_sha256"] for row in downloads)

    confirmed_rows = [
        row for row in diagnostic_rows
        if _as_bool(row["positioning_cycle_confirmed"])
    ]
    assert len(confirmed_rows) == primary_count
    assert all(
        row["reason_code"] == "OPEN_INTEREST_BUILD_AND_RELEASE_CONFIRMED"
        for row in confirmed_rows
    )
    assert all(
        row["scenario_id"].endswith(":oi-build-release-primary")
        for row in primary_rows
    )
    assert all(
        row["scenario_id"].endswith(":v23-mss-control")
        for row in control_rows
    )
    expected_selected = (
        primary_count
        if summary["rule"] == "oi-build-release-primary"
        else control_count
    )
    assert selected_count == expected_selected
    selected_suffix = (
        ":oi-build-release-primary"
        if summary["rule"] == "oi-build-release-primary"
        else ":v23-mss-control"
    )
    assert all(row["scenario_id"].endswith(selected_suffix) for row in selected_rows)

    for row in metric_rows:
        observation = int(row["observation_time_ns"])
        available = int(row["available_time_ns"])
        assert available - observation == 300_000_000_000

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
        "candidate_version": 35,
        "rule": summary["rule"],
        "classification": classification,
        "advance": full_pass or promising,
        "operational_gate": operational,
        "source_v23_plan_count": source_count,
        "primary_plan_count": primary_count,
        "control_plan_count": control_count,
        "selected_plan_count": selected_count,
        "positioning_cycle_confirmed_count": confirmed_count,
        "positioning_reason_counts": summary["positioning_reason_counts"],
        "positioning_metric_observations": int(
            summary["positioning_metric_observations"],
        ),
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
