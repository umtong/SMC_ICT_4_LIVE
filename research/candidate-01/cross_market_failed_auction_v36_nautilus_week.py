#!/usr/bin/env python3
"""Authoritative NautilusTrader evaluation for candidate 01 v36.

Primary
    Cost-resolved USD-M futures sweep of a frozen joint 30-minute balance,
    no cost-resolved spot confirmation through the corresponding spot boundary,
    then a completed futures re-entry with opposite aggressive flow.

Control
    Identical futures sweep, failure, entry, stop, target, risk, cost and hold;
    removes only spot non-confirmation.

Official futures aggregate trades are represented one-for-one as NautilusTrader
TradeTicks. NautilusTrader exclusively owns orders, fills, commissions, margin,
positions, PnL, NAV and reports. ``--rule both`` launches the primary and
control as separate Python processes. This is required because NautilusTrader
1.230.0 owns a process-global Rust logger which cannot be initialized twice in
one interpreter. Both child runs reuse the same checksum-verified archive cache
and deterministically rebuild the identical outcome-independent tick stream.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_data import AggTrade, AggTradeDownload, download_aggtrade_days  # noqa: E402
from calendar_mss_displacement_v23_nautilus_week import (  # noqa: E402
    execution_trade_windows,
)
from cross_market_aggtrade_data_v36 import (  # noqa: E402
    download_spot_aggtrade_days,
)
from cross_market_failed_auction_v36 import (  # noqa: E402
    BALANCE_MINUTES,
    CONFIRMATION_MINUTES,
    MIN_STRUCTURE_WIDTH_FRACTION,
    MIN_SWEEP_FRACTION,
    CrossMarketDiagnostic,
    SweepEvent,
    build_cross_market_plans,
    build_joint_minutes,
)
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import MAXIMUM_HOLD_NS  # noqa: E402
from impact_regime_probe import ScenarioPlan  # noqa: E402
from nautilus_plan_backtest import NautilusExecutionConfig  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402

PRIMARY_RULE = "spot-unconfirmed-primary"
CONTROL_RULE = "futures-failure-control"
RULES = (PRIMARY_RULE, CONTROL_RULE)
SUMMARY = "cross_market_failed_auction_v36_summary.json"
CONTEXT_DAYS = 2


@dataclass(slots=True)
class PreparedV36Week:
    execution: NautilusExecutionConfig
    evaluation_start: datetime
    evaluation_end: datetime
    start_ns: int
    end_ns: int
    futures_downloads: list[AggTradeDownload]
    spot_downloads: list[AggTradeDownload]
    minutes: list[Any]
    minute_counts: dict[str, int]
    machine: Any
    primary_plans: list[ScenarioPlan]
    control_plans: list[ScenarioPlan]
    diagnostics: list[CrossMarketDiagnostic]
    sweep_events: list[SweepEvent]
    execution_trades: list[AggTrade]
    execution_windows: list[Any]


def evaluation_plans(
    rows: list[ScenarioPlan],
    *,
    start_ns: int,
    end_ns: int,
) -> list[ScenarioPlan]:
    return [
        row
        for row in rows
        if start_ns <= int(row.signal_time_ns) < end_ns
    ]


def _joint_rows(minutes: list[Any]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in minutes:
        result.append(
            {
                "start_time_ns": row.start_time_ns,
                "end_time_ns": row.end_time_ns,
                "futures_open": row.futures.open,
                "futures_high": row.futures.high,
                "futures_low": row.futures.low,
                "futures_close": row.futures.close,
                "futures_quote_notional": row.futures.quote_notional,
                "futures_signed_aggressive_quote": (
                    row.futures.signed_aggressive_quote
                ),
                "futures_imbalance": row.futures.imbalance,
                "futures_trade_count": row.futures.trade_count,
                "futures_first_trade_time_ns": row.futures.first_trade_time_ns,
                "futures_last_trade_time_ns": row.futures.last_trade_time_ns,
                "spot_open": row.spot.open,
                "spot_high": row.spot.high,
                "spot_low": row.spot.low,
                "spot_close": row.spot.close,
                "spot_quote_notional": row.spot.quote_notional,
                "spot_signed_aggressive_quote": row.spot.signed_aggressive_quote,
                "spot_imbalance": row.spot.imbalance,
                "spot_trade_count": row.spot.trade_count,
                "spot_first_trade_time_ns": row.spot.first_trade_time_ns,
                "spot_last_trade_time_ns": row.spot.last_trade_time_ns,
            },
        )
    return result


def _write_dataclass_csv(path: Path, rows: list[Any], row_type: type[Any]) -> None:
    columns = [field.name for field in fields(row_type)]
    pd.DataFrame([asdict(row) for row in rows], columns=columns).to_csv(
        path,
        index=False,
    )


def write_v36_evidence(
    output: Path,
    *,
    prepared: PreparedV36Week,
    selected_plans: list[ScenarioPlan],
) -> dict[str, Any]:
    pd.DataFrame(_joint_rows(prepared.minutes)).to_csv(
        output / "joint_minute_bars.csv",
        index=False,
    )
    _write_dataclass_csv(
        output / "cross_market_sweep_events.csv",
        prepared.sweep_events,
        SweepEvent,
    )
    _write_dataclass_csv(
        output / "cross_market_diagnostics.csv",
        prepared.diagnostics,
        CrossMarketDiagnostic,
    )
    _write_dataclass_csv(
        output / "primary_plans.csv",
        prepared.primary_plans,
        ScenarioPlan,
    )
    _write_dataclass_csv(
        output / "control_plans.csv",
        prepared.control_plans,
        ScenarioPlan,
    )
    _write_dataclass_csv(
        output / "scenario_plans.csv",
        selected_plans,
        ScenarioPlan,
    )
    atomic_json(
        output / "futures_aggtrade_downloads.json",
        {"downloads": [row.to_dict() for row in prepared.futures_downloads]},
    )
    atomic_json(
        output / "spot_aggtrade_downloads.json",
        {"downloads": [row.to_dict() for row in prepared.spot_downloads]},
    )
    return {
        **prepared.minute_counts,
        "state_machine_counts": dict(prepared.machine.counts),
        "joint_minute_bars_written": len(prepared.minutes),
        "cross_market_sweep_event_count": len(prepared.sweep_events),
        "cross_market_diagnostic_count": len(prepared.diagnostics),
        "primary_plan_count": len(prepared.primary_plans),
        "control_plan_count": len(prepared.control_plans),
        "futures_download_count": len(prepared.futures_downloads),
        "spot_download_count": len(prepared.spot_downloads),
        "futures_checksums_match": all(
            row.sha256 == row.expected_sha256
            for row in prepared.futures_downloads
        ),
        "spot_checksums_match": all(
            row.sha256 == row.expected_sha256 for row in prepared.spot_downloads
        ),
    }


def prepare_week(args: argparse.Namespace) -> PreparedV36Week:
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_DAYS)
    download_end = evaluation_end + timedelta(minutes=1)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)
    context_start_ns = int(pd.Timestamp(context_start).as_unit("ns").value)

    futures_downloads = download_aggtrade_days(
        symbol="BTCUSDT",
        start=context_start,
        end=download_end,
        cache_dir=args.cache / "futures",
        workers=args.workers,
    )
    spot_downloads = download_spot_aggtrade_days(
        symbol="BTCUSDT",
        start=context_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    minutes, minute_counts = build_joint_minutes(
        futures_records=futures_downloads,
        spot_records=spot_downloads,
        start_ns=context_start_ns,
        end_ns=end_ns,
    )
    if not minutes:
        raise RuntimeError("no synchronized official spot/futures minutes")
    machine = build_cross_market_plans(minutes)
    primary_plans = evaluation_plans(
        machine.primary_plans,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    control_plans = evaluation_plans(
        machine.control_plans,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    diagnostics = [
        row
        for row in machine.diagnostics
        if (
            start_ns <= int(row.sweep_time_ns) < end_ns
            or (
                row.resolution_time_ns is not None
                and start_ns <= int(row.resolution_time_ns) < end_ns
            )
        )
    ]
    sweep_events = [
        row
        for row in machine.sweep_events
        if start_ns <= int(row.event_time_ns) < end_ns
    ]
    if len(primary_plans) > len(control_plans):
        raise RuntimeError("v36 primary cannot exceed control")
    primary_sources = {
        row.scenario_id.removesuffix(":spot-unconfirmed-primary")
        for row in primary_plans
    }
    control_sources = {
        row.scenario_id.removesuffix(":futures-failure-control")
        for row in control_plans
    }
    if not primary_sources.issubset(control_sources):
        raise RuntimeError("every evaluation primary plan requires a control plan")

    union = {plan.scenario_id: plan for plan in [*primary_plans, *control_plans]}
    union_plans = sorted(
        union.values(),
        key=lambda row: (row.signal_time_ns, row.scenario_id),
    )
    execution_trades, windows = execution_trade_windows(
        futures_downloads,
        plans=union_plans,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )
    return PreparedV36Week(
        execution=execution,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        start_ns=start_ns,
        end_ns=end_ns,
        futures_downloads=futures_downloads,
        spot_downloads=spot_downloads,
        minutes=minutes,
        minute_counts=minute_counts,
        machine=machine,
        primary_plans=primary_plans,
        control_plans=control_plans,
        diagnostics=diagnostics,
        sweep_events=sweep_events,
        execution_trades=execution_trades,
        execution_windows=windows,
    )


def run_rule(
    prepared: PreparedV36Week,
    *,
    rule: str,
    output: Path,
) -> dict[str, Any]:
    if rule not in RULES:
        raise ValueError(f"unknown v36 rule: {rule}")
    selected_plans = (
        prepared.primary_plans
        if rule == PRIMARY_RULE
        else prepared.control_plans
    )
    output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_tick_plan_backtest(
        label=(
            f"BTCUSDT-v36-{rule}-"
            f"{prepared.evaluation_start.date().isoformat()}-7d"
        ),
        trades=prepared.execution_trades,
        plans=selected_plans,
        evaluation_start=prepared.evaluation_start,
        evaluation_end=prepared.evaluation_end,
        execution=prepared.execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output,
    )
    diagnostics_summary = write_v36_evidence(
        output,
        prepared=prepared,
        selected_plans=selected_plans,
    )
    payload = {
        "candidate": "spot-unconfirmed futures external-liquidity failed auction",
        "candidate_version": 36,
        "rule": rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "TradeTick",
        "confirmation_data": (
            "official Binance Vision BTCUSDT spot and USD-M futures aggTrades"
        ),
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": prepared.evaluation_start.isoformat(),
        "evaluation_end_utc": prepared.evaluation_end.isoformat(),
        "context_days": CONTEXT_DAYS,
        "expected_joint_minute_count": (CONTEXT_DAYS + 7) * 24 * 60,
        "bar_availability": "exact UTC minute end",
        "balance_minutes": BALANCE_MINUTES,
        "confirmation_minutes": CONFIRMATION_MINUTES,
        "minimum_structure_width_fraction": MIN_STRUCTURE_WIDTH_FRACTION,
        "minimum_sweep_fraction": MIN_SWEEP_FRACTION,
        "primary_variable": (
            "no cost-resolved spot excursion through the corresponding frozen "
            "spot balance boundary before completed futures failure"
            if rule == PRIMARY_RULE
            else "single ablation removes only spot non-confirmation"
        ),
        "scenario_sequence": (
            "joint two-sided 30-minute balance -> cost-resolved futures external "
            "sweep with aligned aggressive flow -> within three later completed "
            "minutes futures closes back inside the frozen boundary with "
            "opposite aggressive flow -> first later official futures TradeTick"
        ),
        "invalidation": (
            "full observed futures sweep extreme plus one 7-bp all-in cost buffer"
        ),
        "target": "opposite frozen futures balance boundary",
        "leadership_policy": (
            "no market is assumed to lead unconditionally; spot is only a "
            "cross-market confirmation variable after futures itself fails"
        ),
        "selected_plan_count": len(selected_plans),
        "selected_side_counts": dict(
            Counter(plan.side.value for plan in selected_plans)
        ),
        **diagnostics_summary,
        "official_execution_trade_ticks": len(prepared.execution_trades),
        "execution_tick_windows": [list(row) for row in prepared.execution_windows],
        "tick_selection": (
            "outcome-independent union-plan windows plus first official futures "
            "trade of each evaluation UTC day for NAV marking"
        ),
        "risk_fraction": prepared.execution.risk_fraction,
        "all_in_cost_bps_per_side": (
            prepared.execution.all_in_cost_bps_per_side
        ),
        "maximum_hold_hours": MAXIMUM_HOLD_NS / 3_600_000_000_000,
        "metrics": evidence.metrics,
        "long_evaluation_run": False,
    }
    atomic_json(output / SUMMARY, payload)
    return payload


def run(args: argparse.Namespace) -> int:
    if args.rule == "both":
        outputs = {
            PRIMARY_RULE: args.output / "primary",
            CONTROL_RULE: args.output / "control",
        }
        script = Path(__file__).resolve()
        for rule in RULES:
            command = [
                sys.executable,
                str(script),
                "--week",
                args.week,
                "--rule",
                rule,
                "--execution-config",
                str(args.execution_config),
                "--cache",
                str(args.cache),
                "--output",
                str(outputs[rule]),
                "--workers",
                str(args.workers),
            ]
            subprocess.run(command, check=True)
        payloads = {
            rule: json.loads((output / SUMMARY).read_text(encoding="utf-8"))
            for rule, output in outputs.items()
        }
        print(
            json.dumps(
                {
                    "candidate_version": 36,
                    "mode": "both-separate-processes",
                    "week": args.week,
                    "outputs": {rule: str(path) for rule, path in outputs.items()},
                    "metrics": {
                        rule: payload["metrics"] for rule, payload in payloads.items()
                    },
                },
                indent=2,
                sort_keys=True,
            ),
        )
        return 0
    prepared = prepare_week(args)
    payload = run_rule(prepared, rule=args.rule, output=args.output)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--rule", required=True, choices=(*RULES, "both"))
    parser.add_argument(
        "--execution-config",
        type=Path,
        default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-v36-cross-market",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v36-cross-market",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
