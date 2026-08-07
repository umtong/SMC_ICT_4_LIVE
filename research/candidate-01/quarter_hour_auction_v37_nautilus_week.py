#!/usr/bin/env python3
"""Authoritative NautilusTrader week evaluation for candidate 01 v37.

``--rule both`` launches primary and control in separate Python processes to
respect NautilusTrader 1.230.0's process-global Rust logger. Both processes
reuse the same checksum-verified archive cache and deterministically rebuild the
same outcome-independent official TradeTick stream.
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
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import MAXIMUM_HOLD_NS  # noqa: E402
from impact_regime_probe import ScenarioPlan  # noqa: E402
from nautilus_plan_backtest import NautilusExecutionConfig  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from quarter_hour_auction_v37 import (  # noqa: E402
    BALANCE_MINUTES,
    CONTROL_RULE,
    COST_PER_SIDE,
    LIQUIDITY_LOOKBACK_MINUTES,
    MIN_BALANCE_WIDTH_FRACTION,
    PRIMARY_RULE,
    SWING_RADIUS,
    ClockAuctionDiagnostic,
    ClockAuctionPattern,
    FiveMinuteBar,
    build_clock_minutes,
    build_quarter_hour_auction_plans,
)
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402

RULES = (PRIMARY_RULE, CONTROL_RULE)
SUMMARY = "quarter_hour_auction_v37_summary.json"
CONTEXT_DAYS = 4


@dataclass(slots=True)
class Prepared:
    execution: NautilusExecutionConfig
    evaluation_start: datetime
    evaluation_end: datetime
    start_ns: int
    end_ns: int
    downloads: list[AggTradeDownload]
    minutes: list[Any]
    minute_counts: dict[str, int]
    result: Any
    primary_plans: list[ScenarioPlan]
    control_plans: list[ScenarioPlan]
    patterns: list[ClockAuctionPattern]
    diagnostics: list[ClockAuctionDiagnostic]
    five_minute_bars: list[FiveMinuteBar]
    execution_trades: list[AggTrade]
    execution_windows: list[Any]


def in_evaluation(rows, *, start_ns: int, end_ns: int, time_field: str):
    return [
        row
        for row in rows
        if start_ns <= int(getattr(row, time_field)) < end_ns
    ]


def _write_dataclass_csv(path: Path, rows: list[Any], row_type: type[Any]) -> None:
    columns = [field.name for field in fields(row_type)]
    pd.DataFrame([asdict(row) for row in rows], columns=columns).to_csv(
        path,
        index=False,
    )


def _minute_rows(minutes: list[Any]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in minutes:
        impulse = row.first_ten_seconds
        payload: dict[str, object] = {
            "start_time_ns": row.start_time_ns,
            "end_time_ns": row.end_time_ns,
            "open": row.minute.open,
            "high": row.minute.high,
            "low": row.minute.low,
            "close": row.minute.close,
            "quote_notional": row.minute.quote_notional,
            "signed_aggressive_quote": row.minute.signed_aggressive_quote,
            "imbalance": row.minute.imbalance,
            "trade_count": row.minute.trade_count,
            "first_trade_time_ns": row.minute.first_trade_time_ns,
            "last_trade_time_ns": row.minute.last_trade_time_ns,
            "impulse_present": impulse is not None,
        }
        for field in (
            "open",
            "high",
            "low",
            "close",
            "quote_notional",
            "signed_aggressive_quote",
            "imbalance",
            "trade_count",
            "first_trade_time_ns",
            "last_trade_time_ns",
        ):
            payload["impulse_" + field] = (
                getattr(impulse, field) if impulse is not None else None
            )
        payload["impulse_start_time_ns"] = (
            impulse.start_time_ns if impulse is not None else None
        )
        payload["impulse_end_time_ns"] = (
            impulse.end_time_ns if impulse is not None else None
        )
        result.append(payload)
    return result


def prepare(args: argparse.Namespace) -> Prepared:
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_DAYS)
    download_end = evaluation_end + timedelta(minutes=1)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)
    context_ns = int(pd.Timestamp(context_start).as_unit("ns").value)
    downloads = download_aggtrade_days(
        symbol="BTCUSDT",
        start=context_start,
        end=download_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    minutes, minute_counts = build_clock_minutes(
        downloads,
        start_ns=context_ns,
        end_ns=end_ns,
    )
    result = build_quarter_hour_auction_plans(minutes)
    primary = in_evaluation(
        result.primary_plans,
        start_ns=start_ns,
        end_ns=end_ns,
        time_field="signal_time_ns",
    )
    control = in_evaluation(
        result.control_plans,
        start_ns=start_ns,
        end_ns=end_ns,
        time_field="signal_time_ns",
    )
    patterns = in_evaluation(
        result.patterns,
        start_ns=start_ns,
        end_ns=end_ns,
        time_field="signal_time_ns",
    )
    diagnostics = in_evaluation(
        result.diagnostics,
        start_ns=start_ns,
        end_ns=end_ns,
        time_field="signal_time_ns",
    )
    five_minute_bars = [
        row
        for row in result.five_minute_bars
        if context_ns <= row.start_time_ns < end_ns
    ]
    union = {
        plan.scenario_id: plan
        for plan in [*primary, *control]
    }
    union_plans = sorted(
        union.values(),
        key=lambda plan: (plan.signal_time_ns, plan.scenario_id),
    )
    execution_trades, windows = execution_trade_windows(
        downloads,
        plans=union_plans,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )
    return Prepared(
        execution=execution,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        start_ns=start_ns,
        end_ns=end_ns,
        downloads=downloads,
        minutes=minutes,
        minute_counts=minute_counts,
        result=result,
        primary_plans=primary,
        control_plans=control,
        patterns=patterns,
        diagnostics=diagnostics,
        five_minute_bars=five_minute_bars,
        execution_trades=execution_trades,
        execution_windows=windows,
    )


def write_evidence(
    output: Path,
    prepared: Prepared,
    selected: list[ScenarioPlan],
) -> dict[str, Any]:
    pd.DataFrame(_minute_rows(prepared.minutes)).to_csv(
        output / "clock_minute_bars.csv",
        index=False,
    )
    _write_dataclass_csv(
        output / "five_minute_bars.csv",
        prepared.five_minute_bars,
        FiveMinuteBar,
    )
    _write_dataclass_csv(
        output / "clock_auction_patterns.csv",
        prepared.patterns,
        ClockAuctionPattern,
    )
    _write_dataclass_csv(
        output / "clock_auction_diagnostics.csv",
        prepared.diagnostics,
        ClockAuctionDiagnostic,
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
        selected,
        ScenarioPlan,
    )
    atomic_json(
        output / "aggtrade_downloads.json",
        {"downloads": [row.to_dict() for row in prepared.downloads]},
    )
    return {
        **prepared.minute_counts,
        "state_machine_counts": dict(prepared.result.counts),
        "clock_minute_bars_written": len(prepared.minutes),
        "five_minute_bar_count": len(prepared.five_minute_bars),
        "pattern_count": len(prepared.patterns),
        "diagnostic_count": len(prepared.diagnostics),
        "primary_plan_count": len(prepared.primary_plans),
        "control_plan_count": len(prepared.control_plans),
        "download_count": len(prepared.downloads),
        "checksums_match": all(
            row.sha256 == row.expected_sha256 for row in prepared.downloads
        ),
    }


def run_rule(
    prepared: Prepared,
    *,
    rule: str,
    output: Path,
) -> dict[str, Any]:
    selected = (
        prepared.primary_plans
        if rule == PRIMARY_RULE
        else prepared.control_plans
    )
    output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_tick_plan_backtest(
        label=(
            f"BTCUSDT-v37-{rule}-"
            f"{prepared.evaluation_start.date().isoformat()}-7d"
        ),
        trades=prepared.execution_trades,
        plans=selected,
        evaluation_start=prepared.evaluation_start,
        evaluation_end=prepared.evaluation_end,
        execution=prepared.execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output,
    )
    diagnostic = write_evidence(output, prepared, selected)
    payload = {
        "candidate": "quarter-hour algorithmic-auction continuation",
        "candidate_version": 37,
        "rule": rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "TradeTick",
        "confirmation_data": (
            "official checksum-verified Binance Vision BTCUSDT USD-M aggTrades"
        ),
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": prepared.evaluation_start.isoformat(),
        "evaluation_end_utc": prepared.evaluation_end.isoformat(),
        "context_days": CONTEXT_DAYS,
        "expected_minute_count": (CONTEXT_DAYS + 7) * 24 * 60,
        "bar_availability": (
            "scenario available only at exact UTC boundary-minute end"
        ),
        "balance_minutes": BALANCE_MINUTES,
        "impulse_seconds": 10,
        "minimum_balance_width_fraction": MIN_BALANCE_WIDTH_FRACTION,
        "minimum_displacement_fraction": COST_PER_SIDE,
        "liquidity_lookback_hours": LIQUIDITY_LOOKBACK_MINUTES // 60,
        "liquidity_swing_radius_bars": SWING_RADIUS,
        "scenario_sequence": (
            "prior two-sided range -> first-ten-second cost-resolved displacement "
            "with aligned flow -> completed-minute acceptance -> next official "
            "TradeTick -> nearest confirmed unswept external swing"
        ),
        "invalidation": (
            "one 7-bp buffer beyond frozen dealing-range midpoint"
        ),
        "target": (
            "nearest causally confirmed unswept five-minute swing beyond the "
            "signal-minute extreme"
        ),
        "selected_plan_count": len(selected),
        "selected_side_counts": dict(
            Counter(plan.side.value for plan in selected)
        ),
        **diagnostic,
        "official_execution_trade_ticks": len(prepared.execution_trades),
        "execution_tick_windows": [
            list(row) for row in prepared.execution_windows
        ],
        "tick_selection": (
            "outcome-independent union-plan windows plus first official trade "
            "of each UTC evaluation day"
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
            subprocess.run(
                [
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
                ],
                check=True,
            )
        payloads = {
            rule: json.loads((path / SUMMARY).read_text(encoding="utf-8"))
            for rule, path in outputs.items()
        }
        print(
            json.dumps(
                {
                    "candidate_version": 37,
                    "mode": "both-separate-processes",
                    "week": args.week,
                    "outputs": {
                        rule: str(path) for rule, path in outputs.items()
                    },
                    "metrics": {
                        rule: payload["metrics"]
                        for rule, payload in payloads.items()
                    },
                },
                indent=2,
                sort_keys=True,
            ),
        )
        return 0
    prepared = prepare(args)
    payload = run_rule(prepared, rule=args.rule, output=args.output)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--week", required=True)
    result.add_argument("--rule", required=True, choices=(*RULES, "both"))
    result.add_argument(
        "--execution-config",
        type=Path,
        default=HERE / "nautilus_execution.json",
    )
    result.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-v37-quarter-hour",
    )
    result.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v37-quarter-hour",
    )
    result.add_argument("--workers", type=int, default=4)
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
