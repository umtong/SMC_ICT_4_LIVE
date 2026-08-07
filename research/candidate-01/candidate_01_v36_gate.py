#!/usr/bin/env python3
"""Strict causality, execution and first-week performance gate for v36."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

SUMMARY = "cross_market_failed_auction_v36_summary.json"
MINUTE_NS = 60_000_000_000
BALANCE_MINUTES = 30
CONFIRMATION_MINUTES = 3
MIN_STRUCTURE_WIDTH_FRACTION = 0.0028
MIN_SWEEP_FRACTION = 0.0007
PRIMARY_SUFFIX = ":spot-unconfirmed-primary"
CONTROL_SUFFIX = ":futures-failure-control"
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
    "joint_minute_bars.csv",
    "cross_market_sweep_events.csv",
    "cross_market_diagnostics.csv",
    "primary_plans.csv",
    "control_plans.csv",
    "scenario_plans.csv",
    "futures_aggtrade_downloads.json",
    "spot_aggtrade_downloads.json",
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


def _as_optional_int(raw: str) -> int | None:
    text = raw.strip()
    return int(float(text)) if text else None


def _as_optional_float(raw: str) -> float | None:
    text = raw.strip()
    return float(text) if text else None


def _source_id(scenario_id: str) -> str:
    return scenario_id.removesuffix(PRIMARY_SUFFIX).removesuffix(CONTROL_SUFFIX)


def _assert_close(actual: float, expected: float, *, tolerance: float = 1e-12) -> None:
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
    futures_downloads = load(root / "futures_aggtrade_downloads.json")["downloads"]
    spot_downloads = load(root / "spot_aggtrade_downloads.json")["downloads"]
    nav_rows = read_csv(root / "daily_nav.csv")
    minute_rows = read_csv(root / "joint_minute_bars.csv")
    event_rows = read_csv(root / "cross_market_sweep_events.csv")
    diagnostic_rows = read_csv(root / "cross_market_diagnostics.csv")
    primary_rows = read_csv(root / "primary_plans.csv")
    control_rows = read_csv(root / "control_plans.csv")
    selected_rows = read_csv(root / "scenario_plans.csv")

    assert summary["authoritative_backtest"] is True
    assert summary["execution_engine"] == "NautilusTrader"
    assert summary["execution_data_type"] == "TradeTick"
    assert summary["custom_fill_simulator"] is False
    assert summary["custom_pnl_or_nav_ledger"] is False
    assert int(summary["candidate_version"]) == 36
    assert summary["rule"] in (
        "spot-unconfirmed-primary",
        "futures-failure-control",
    )
    assert int(summary["context_days"]) == 2
    assert summary["bar_availability"] == "exact UTC minute end"
    assert int(summary["balance_minutes"]) == BALANCE_MINUTES
    assert int(summary["confirmation_minutes"]) == CONFIRMATION_MINUTES
    _assert_close(
        float(summary["minimum_structure_width_fraction"]),
        MIN_STRUCTURE_WIDTH_FRACTION,
    )
    _assert_close(
        float(summary["minimum_sweep_fraction"]),
        MIN_SWEEP_FRACTION,
    )
    assert float(summary["risk_fraction"]) == 0.03
    assert float(summary["all_in_cost_bps_per_side"]) == 7.0
    assert float(summary["maximum_hold_hours"]) == 4.0
    assert summary["long_evaluation_run"] is False
    assert summary["futures_checksums_match"] is True
    assert summary["spot_checksums_match"] is True

    expected_minutes = int(summary["expected_joint_minute_count"])
    assert expected_minutes == 9 * 24 * 60
    assert int(summary["futures_minute_count"]) == expected_minutes
    assert int(summary["spot_minute_count"]) == expected_minutes
    assert int(summary["joint_minute_count"]) == expected_minutes
    assert int(summary["joint_minute_bars_written"]) == expected_minutes
    assert int(summary["futures_only_minutes"]) == 0
    assert int(summary["spot_only_minutes"]) == 0
    assert int(summary["joint_time_gaps"]) == 0
    assert len(minute_rows) == expected_minutes

    first_start = int(minute_rows[0]["start_time_ns"])
    prior_start: int | None = None
    for row in minute_rows:
        start = int(row["start_time_ns"])
        end = int(row["end_time_ns"])
        assert end - start == MINUTE_NS
        if prior_start is not None:
            assert start - prior_start == MINUTE_NS
        prior_start = start
        for prefix in ("futures", "spot"):
            first_trade = int(row[f"{prefix}_first_trade_time_ns"])
            last_trade = int(row[f"{prefix}_last_trade_time_ns"])
            assert start <= first_trade <= last_trade < end
            assert int(row[f"{prefix}_trade_count"]) > 0
            assert float(row[f"{prefix}_quote_notional"]) > 0.0
            for field in ("open", "high", "low", "close"):
                assert float(row[f"{prefix}_{field}"]) > 0.0
    assert int(minute_rows[-1]["end_time_ns"]) - first_start == (
        expected_minutes * MINUTE_NS
    )

    assert len(futures_downloads) == int(summary["futures_download_count"])
    assert len(spot_downloads) == int(summary["spot_download_count"])
    assert futures_downloads and spot_downloads
    assert all(row["sha256"] == row["expected_sha256"] for row in futures_downloads)
    assert all(row["sha256"] == row["expected_sha256"] for row in spot_downloads)

    event_by_source: dict[str, dict[str, str]] = {}
    for row in event_rows:
        source = row["scenario_id"]
        assert source not in event_by_source
        event_by_source[source] = row
        event_time = int(row["event_time_ns"])
        balance_start = int(row["balance_start_time_ns"])
        balance_end = int(row["balance_end_time_ns"])
        assert event_time - balance_end == MINUTE_NS
        assert balance_end - balance_start == BALANCE_MINUTES * MINUTE_NS
        assert int(row["futures_midpoint_crosses"]) >= 2
        assert int(row["spot_midpoint_crosses"]) >= 2
        assert float(row["futures_balance_width_fraction"]) >= (
            MIN_STRUCTURE_WIDTH_FRACTION
        )
        assert float(row["spot_balance_width_fraction"]) >= (
            MIN_STRUCTURE_WIDTH_FRACTION
        )
        assert float(row["futures_excursion_fraction"]) >= MIN_SWEEP_FRACTION
        outward = row["outward_side"]
        assert outward in {"LONG", "SHORT"}
        expected_reversal = "SHORT" if outward == "LONG" else "LONG"
        assert row["reversal_side"] == expected_reversal
        futures_imbalance = float(row["futures_imbalance"])
        assert futures_imbalance > 0.0 if outward == "LONG" else futures_imbalance < 0.0

    diagnostic_by_source: dict[str, dict[str, str]] = {}
    confirmed_rows: list[dict[str, str]] = []
    for row in diagnostic_rows:
        source = row["scenario_id"]
        assert source not in diagnostic_by_source
        diagnostic_by_source[source] = row
        sweep_time = int(row["sweep_time_ns"])
        expiry = int(row["expiry_time_ns"])
        assert expiry - sweep_time == CONFIRMATION_MINUTES * MINUTE_NS
        assert int(row["futures_midpoint_crosses"]) >= 2
        assert int(row["spot_midpoint_crosses"]) >= 2
        assert float(row["futures_balance_width_fraction"]) >= (
            MIN_STRUCTURE_WIDTH_FRACTION
        )
        assert float(row["spot_balance_width_fraction"]) >= (
            MIN_STRUCTURE_WIDTH_FRACTION
        )
        assert float(row["futures_sweep_excursion_fraction"]) >= (
            MIN_SWEEP_FRACTION
        )
        confirmed = _as_bool(row["futures_failed_auction_confirmed"])
        resolution = _as_optional_int(row["resolution_time_ns"])
        minutes_to_resolution = _as_optional_int(row["minutes_to_resolution"])
        primary_id = row["primary_plan_id"].strip()
        control_id = row["control_plan_id"].strip()
        spot_confirmed = _as_bool(row["spot_confirmed_before_resolution"])
        spot_confirmation_time = _as_optional_int(row["spot_confirmation_time_ns"])
        maximum_spot_excursion = float(row["maximum_spot_excursion_fraction"])
        if spot_confirmed:
            assert spot_confirmation_time is not None
            assert sweep_time <= spot_confirmation_time <= expiry
            assert maximum_spot_excursion >= MIN_SWEEP_FRACTION
        else:
            assert spot_confirmation_time is None
            assert maximum_spot_excursion < MIN_SWEEP_FRACTION
        if confirmed:
            confirmed_rows.append(row)
            assert resolution is not None
            assert sweep_time < resolution <= expiry
            assert minutes_to_resolution in {1, 2, 3}
            assert control_id.endswith(CONTROL_SUFFIX)
            assert row["reason_code"] in {
                "SPOT_UNCONFIRMED_FUTURES_FAILED_AUCTION",
                "SPOT_CONFIRMED_FUTURES_FAILED_AUCTION_CONTROL_ONLY",
            }
            failure_imbalance = _as_optional_float(row["failure_imbalance"])
            failure_close = _as_optional_float(row["failure_close"])
            rejection_ratio = _as_optional_float(
                row["full_excursion_rejection_ratio"],
            )
            assert failure_imbalance is not None
            assert failure_close is not None
            assert rejection_ratio is not None and rejection_ratio > 1.0
            if row["outward_side"] == "LONG":
                assert failure_close < float(row["futures_boundary"])
                assert failure_imbalance < 0.0
            else:
                assert failure_close > float(row["futures_boundary"])
                assert failure_imbalance > 0.0
            if spot_confirmed:
                assert not primary_id
            else:
                assert primary_id.endswith(PRIMARY_SUFFIX)
        else:
            assert resolution is None
            assert minutes_to_resolution is None
            assert not primary_id and not control_id
            assert row["reason_code"] in {
                "FUTURES_SWEEP_RESPONSE_WINDOW_EXPIRED",
                "DATA_END_BEFORE_RESPONSE_RESOLUTION",
            }

    primary_count = int(summary["primary_plan_count"])
    control_count = int(summary["control_plan_count"])
    selected_count = int(summary["selected_plan_count"])
    assert len(primary_rows) == primary_count
    assert len(control_rows) == control_count
    assert len(selected_rows) == selected_count
    assert len(confirmed_rows) == control_count
    assert int(summary["cross_market_diagnostic_count"]) == len(diagnostic_rows)
    assert int(summary["cross_market_sweep_event_count"]) == len(event_rows)

    primary_sources = {_source_id(row["scenario_id"]) for row in primary_rows}
    control_sources = {_source_id(row["scenario_id"]) for row in control_rows}
    assert primary_sources.issubset(control_sources)
    assert len(primary_sources) == len(primary_rows)
    assert len(control_sources) == len(control_rows)
    assert all(row["scenario_id"].endswith(PRIMARY_SUFFIX) for row in primary_rows)
    assert all(row["scenario_id"].endswith(CONTROL_SUFFIX) for row in control_rows)

    for row in [*primary_rows, *control_rows]:
        source = _source_id(row["scenario_id"])
        diagnostic = diagnostic_by_source[source]
        assert _as_bool(diagnostic["futures_failed_auction_confirmed"])
        resolution_time_ns = _as_optional_int(
            diagnostic["resolution_time_ns"],
        )
        assert resolution_time_ns is not None
        assert int(row["signal_time_ns"]) == resolution_time_ns
        assert row["side"] == diagnostic["reversal_side"]
        _assert_close(
            float(row["target_price"]),
            float(diagnostic["futures_opposite_boundary"]),
        )
        _assert_close(
            float(row["confirmation_hold_price"]),
            float(diagnostic["futures_boundary"]),
        )
        if row["side"] == "SHORT":
            _assert_close(
                float(row["stop_price"]),
                float(diagnostic["futures_sweep_high"]) * (1.0 + MIN_SWEEP_FRACTION),
            )
        else:
            _assert_close(
                float(row["stop_price"]),
                float(diagnostic["futures_sweep_low"]) * (1.0 - MIN_SWEEP_FRACTION),
            )

    expected_selected = (
        primary_count
        if summary["rule"] == "spot-unconfirmed-primary"
        else control_count
    )
    assert selected_count == expected_selected
    selected_suffix = (
        PRIMARY_SUFFIX
        if summary["rule"] == "spot-unconfirmed-primary"
        else CONTROL_SUFFIX
    )
    assert all(row["scenario_id"].endswith(selected_suffix) for row in selected_rows)

    assert metrics["execution_engine"] == "NautilusTrader"
    assert metrics["trade_execution"] is True
    assert metrics["custom_fill_simulator"] is False
    assert metrics["custom_pnl_or_nav_ledger"] is False
    assert float(metrics["risk_fraction"]) == 0.03
    assert float(metrics["all_in_cost_bps_per_side"]) == 7.0
    assert contract["authoritative_performance_engine"] == "NautilusTrader"
    assert contract["custom_fill_simulator"] is False
    assert contract["custom_pnl_or_nav_ledger"] is False
    assert contract["one_global_position"] is True

    nav_dates = [row["date"] for row in nav_rows]
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
        "candidate_version": 36,
        "rule": summary["rule"],
        "classification": classification,
        "advance": full_pass or promising,
        "operational_gate": operational,
        "joint_minute_count": len(minute_rows),
        "sweep_event_count": len(event_rows),
        "diagnostic_count": len(diagnostic_rows),
        "confirmed_failed_auction_count": len(confirmed_rows),
        "primary_plan_count": primary_count,
        "control_plan_count": control_count,
        "selected_plan_count": selected_count,
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
