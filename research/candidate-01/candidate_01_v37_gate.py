#!/usr/bin/env python3
"""Strict causality, clock, liquidity-ledger and performance gate for v37."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

SUMMARY = "quarter_hour_auction_v37_summary.json"
MINUTE_NS = 60_000_000_000
TEN_SECONDS_NS = 10_000_000_000
BALANCE_MINUTES = 15
COST = 0.0007
MIN_WIDTH = 0.0028
PRIMARY = "quarter-hour-clock-primary"
CONTROL = "ordinary-five-minute-clock-control"
PRIMARY_SUFFIX = ":quarter-hour-clock-primary"
CONTROL_SUFFIX = ":ordinary-five-minute-clock-control"
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
    "clock_minute_bars.csv",
    "five_minute_bars.csv",
    "clock_auction_patterns.csv",
    "clock_auction_diagnostics.csv",
    "primary_plans.csv",
    "control_plans.csv",
    "scenario_plans.csv",
    "aggtrade_downloads.json",
)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def expected_dates(week: str) -> list[str]:
    start = date.fromisoformat(week)
    return [(start + timedelta(days=index)).isoformat() for index in range(7)]


def as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "t", "yes"}


def optional_int(value: str) -> int | None:
    text = str(value).strip()
    return int(float(text)) if text else None


def optional_float(value: str) -> float | None:
    text = str(value).strip()
    return float(text) if text else None


def profit_factor_gate(metrics: dict[str, Any]) -> float:
    raw = metrics.get("profit_factor")
    if raw is not None:
        return float(raw)
    trades = int(metrics.get("closed_positions", 0))
    win_rate = float(metrics.get("win_rate") or 0.0)
    return 999.0 if trades > 0 and win_rate == 1.0 else 0.0


def assert_close(actual: float, expected: float, tolerance: float = 1e-12) -> None:
    assert abs(actual - expected) <= tolerance * max(1.0, abs(expected)), (
        actual,
        expected,
    )


def classify(root: Path, week: str) -> dict[str, Any]:
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"missing authoritative evidence: {missing}")

    summary = load(root / SUMMARY)
    metrics = load(root / "nautilus_metrics.json")
    contract = load(root / "execution_contract.json")
    downloads = load(root / "aggtrade_downloads.json")["downloads"]
    minute_rows = read_csv(root / "clock_minute_bars.csv")
    five_rows = read_csv(root / "five_minute_bars.csv")
    pattern_rows = read_csv(root / "clock_auction_patterns.csv")
    diagnostic_rows = read_csv(root / "clock_auction_diagnostics.csv")
    primary_rows = read_csv(root / "primary_plans.csv")
    control_rows = read_csv(root / "control_plans.csv")
    selected_rows = read_csv(root / "scenario_plans.csv")

    assert int(summary["candidate_version"]) == 37
    assert summary["rule"] in {PRIMARY, CONTROL}
    assert summary["authoritative_backtest"] is True
    assert summary["execution_engine"] == "NautilusTrader"
    assert summary["execution_data_type"] == "TradeTick"
    assert summary["custom_fill_simulator"] is False
    assert summary["custom_pnl_or_nav_ledger"] is False
    assert int(summary["context_days"]) == 4
    assert int(summary["balance_minutes"]) == BALANCE_MINUTES
    assert int(summary["impulse_seconds"]) == 10
    assert_close(float(summary["minimum_balance_width_fraction"]), MIN_WIDTH)
    assert_close(float(summary["minimum_displacement_fraction"]), COST)
    assert int(summary["liquidity_lookback_hours"]) == 72
    assert int(summary["liquidity_swing_radius_bars"]) == 2
    assert float(summary["risk_fraction"]) == 0.03
    assert float(summary["all_in_cost_bps_per_side"]) == 7.0
    assert float(summary["maximum_hold_hours"]) == 4.0
    assert summary["checksums_match"] is True
    assert summary["long_evaluation_run"] is False

    expected_minutes = 11 * 24 * 60
    assert int(summary["expected_minute_count"]) == expected_minutes
    assert int(summary["minute_count"]) == expected_minutes
    assert int(summary["clock_minute_bars_written"]) == expected_minutes
    assert int(summary["minutes_without_first_ten_second_trade"]) == 0
    assert int(summary["first_ten_second_bar_count"]) == expected_minutes
    assert int(summary["minute_time_gaps"]) == 0
    assert len(minute_rows) == expected_minutes

    prior_start: int | None = None
    for row in minute_rows:
        start = int(row["start_time_ns"])
        end = int(row["end_time_ns"])
        assert end - start == MINUTE_NS
        if prior_start is not None:
            assert start - prior_start == MINUTE_NS
        prior_start = start
        assert as_bool(row["impulse_present"])
        assert optional_int(row["impulse_start_time_ns"]) == start
        assert optional_int(row["impulse_end_time_ns"]) == start + TEN_SECONDS_NS
        assert start <= int(row["first_trade_time_ns"]) <= int(
            row["last_trade_time_ns"],
        ) < end
        impulse_first = optional_int(row["impulse_first_trade_time_ns"])
        impulse_last = optional_int(row["impulse_last_trade_time_ns"])
        assert impulse_first is not None and impulse_last is not None
        assert start <= impulse_first <= impulse_last < start + TEN_SECONDS_NS

    assert len(downloads) == int(summary["download_count"])
    assert downloads
    assert all(
        row["sha256"] == row["expected_sha256"] for row in downloads
    )
    assert len(five_rows) == int(summary["five_minute_bar_count"])
    assert all(
        int(row["end_time_ns"]) - int(row["start_time_ns"])
        == 5 * MINUTE_NS
        for row in five_rows
    )
    assert len(pattern_rows) == int(summary["pattern_count"])
    assert len(diagnostic_rows) == int(summary["diagnostic_count"])
    pattern_ids = {row["scenario_id"] for row in pattern_rows}
    assert len(pattern_ids) == len(pattern_rows)
    assert {row["scenario_id"] for row in diagnostic_rows} == pattern_ids

    planned = 0
    for row in diagnostic_rows:
        signal_time = int(row["signal_time_ns"])
        signal_start = signal_time - MINUTE_NS
        minute_of_hour = int(row["boundary_minute_of_hour"])
        assert minute_of_hour == (signal_start // MINUTE_NS) % 60
        expected_class = PRIMARY if minute_of_hour % 15 == 0 else CONTROL
        assert row["clock_class"] == expected_class
        range_start = int(row["range_start_time_ns"])
        range_end = int(row["range_end_time_ns"])
        assert range_end == signal_start
        assert range_end - range_start == BALANCE_MINUTES * MINUTE_NS
        assert int(row["range_midpoint_crosses"]) >= 2
        assert float(row["range_width_fraction"]) >= MIN_WIDTH
        low = float(row["range_low"])
        high = float(row["range_high"])
        midpoint = float(row["range_midpoint"])
        assert_close(midpoint, 0.5 * (low + high))
        impulse_open = float(row["impulse_open"])
        assert low <= impulse_open <= high
        side = row["side"]
        if side == "LONG":
            assert float(row["impulse_close"]) >= high * (1.0 + COST)
            assert float(row["minute_close"]) >= high * (1.0 + COST)
            assert float(row["impulse_imbalance"]) > 0.0
            assert float(row["minute_imbalance"]) > 0.0
        else:
            assert side == "SHORT"
            assert float(row["impulse_close"]) <= low * (1.0 - COST)
            assert float(row["minute_close"]) <= low * (1.0 - COST)
            assert float(row["impulse_imbalance"]) < 0.0
            assert float(row["minute_imbalance"]) < 0.0

        plan_id = row["plan_id"].strip()
        if not plan_id:
            assert row["reason_code"] == (
                "NO_CAUSALLY_CONFIRMED_UNSWEPT_EXTERNAL_LIQUIDITY"
            )
            continue
        planned += 1
        liquidity_price = optional_float(row["liquidity_price"])
        confirmation_time = optional_int(row["liquidity_confirmation_time_ns"])
        stop = optional_float(row["stop_price"])
        target = optional_float(row["target_price"])
        assert liquidity_price is not None
        assert confirmation_time is not None and confirmation_time <= signal_start
        assert stop is not None and target is not None
        assert_close(target, liquidity_price)
        if side == "LONG":
            assert liquidity_price > float(row["minute_high"])
            assert_close(stop, midpoint * (1.0 - COST))
        else:
            assert liquidity_price < float(row["minute_low"])
            assert_close(stop, midpoint * (1.0 + COST))
        assert optional_float(row["signal_close_price_risk_fraction"]) is not None
        assert optional_float(row["signal_close_net_reward_risk"]) is not None

    assert len(primary_rows) == int(summary["primary_plan_count"])
    assert len(control_rows) == int(summary["control_plan_count"])
    assert planned == len(primary_rows) + len(control_rows)
    assert all(
        row["scenario_id"].endswith(PRIMARY_SUFFIX)
        and row["side"] in {"LONG", "SHORT"}
        for row in primary_rows
    )
    assert all(
        row["scenario_id"].endswith(CONTROL_SUFFIX)
        and row["side"] in {"LONG", "SHORT"}
        for row in control_rows
    )
    expected_selected = (
        len(primary_rows) if summary["rule"] == PRIMARY else len(control_rows)
    )
    assert len(selected_rows) == int(summary["selected_plan_count"])
    assert len(selected_rows) == expected_selected
    selected_suffix = (
        PRIMARY_SUFFIX if summary["rule"] == PRIMARY else CONTROL_SUFFIX
    )
    assert all(
        row["scenario_id"].endswith(selected_suffix)
        for row in selected_rows
    )

    assert metrics["execution_engine"] == "NautilusTrader"
    assert metrics["trade_execution"] is True
    assert metrics["custom_fill_simulator"] is False
    assert metrics["custom_pnl_or_nav_ledger"] is False
    assert float(metrics["risk_fraction"]) == 0.03
    assert float(metrics["all_in_cost_bps_per_side"]) == 7.0
    assert contract["authoritative_performance_engine"] == "NautilusTrader"
    assert contract["one_global_position"] is True
    assert contract["custom_fill_simulator"] is False
    assert contract["custom_pnl_or_nav_ledger"] is False

    nav_rows = read_csv(root / "daily_nav.csv")
    nav_dates = [row["date"] for row in nav_rows]
    assert nav_dates == expected_dates(week)
    trades = int(metrics["closed_positions"])
    win_rate = float(metrics.get("win_rate") or 0.0)
    total_return = float(metrics["total_return"])
    geometric_daily = float(metrics["geometric_mean_daily_return"])
    pf_gate = profit_factor_gate(metrics)
    max_drawdown = float(metrics["max_drawdown"])
    operational = (
        bool(metrics["ended_flat"])
        and int(metrics["one_global_entry_gate_violations"]) == 0
        and int(metrics["protective_order_failures"]) == 0
        and int(metrics["liquidation_marker_rows"]) == 0
    )
    full = (
        operational
        and trades >= 7
        and win_rate >= 0.45
        and total_return > 0.0
        and geometric_daily >= 0.01
        and pf_gate >= 1.20
        and max_drawdown > -0.20
    )
    promising = (
        operational
        and not full
        and trades >= 7
        and win_rate >= 0.45
        and total_return > 0.0
        and geometric_daily >= 0.0075
        and pf_gate >= 1.10
        and max_drawdown > -0.15
    )
    classification = (
        "full_pass"
        if full
        else "promising_but_not_complete"
        if promising
        else "stop"
    )
    gate = {
        "week": week,
        "candidate_version": 37,
        "rule": summary["rule"],
        "classification": classification,
        "advance": full or promising,
        "operational_gate": operational,
        "minute_count": len(minute_rows),
        "pattern_count": len(pattern_rows),
        "diagnostic_count": len(diagnostic_rows),
        "primary_plan_count": len(primary_rows),
        "control_plan_count": len(control_rows),
        "selected_plan_count": len(selected_rows),
        "trades": trades,
        "win_rate": win_rate,
        "total_return": total_return,
        "geometric_daily": geometric_daily,
        "profit_factor": metrics.get("profit_factor"),
        "profit_factor_gate_value": pf_gate,
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
