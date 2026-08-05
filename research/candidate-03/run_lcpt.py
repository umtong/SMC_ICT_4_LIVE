#!/usr/bin/env python3
"""Run frozen candidate-03 LCPT-v1 on one precommitted BTC week."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta, timezone
import json
from math import isinf, isnan
from pathlib import Path
import sys
from typing import Any

CANDIDATE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CANDIDATE_DIR.parents[1]
SRC = REPO_ROOT / "src"
if SRC.is_dir():
    sys.path.insert(0, str(SRC))
sys.path.insert(0, str(CANDIDATE_DIR))

from lcpt_data import (
    AggTradeArchiveStream,
    aggregate_minute_bars,
    load_open_interest_metrics,
)
from lcpt_engine import LcptReplay
from lcpt_features import build_states, detect_cascade_signals
from lcpt_metrics import build_metrics
from lcpt_model import LcptConfig, NS_PER_DAY
from smc_ict_4.contracts import ResearchEvent
from smc_ict_4.event_log import validate_events, write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic


def load_config(path: Path) -> LcptConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    allowed = {item.name for item in fields(LcptConfig)}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown LCPT config fields: {unknown}")
    for name in ("development_weeks", "validation_weeks"):
        if isinstance(payload.get(name), list):
            payload[name] = tuple(payload[name])
    config = LcptConfig(**payload)
    config.validate()
    return config


def date_to_ns(day: date) -> int:
    return int(
        datetime.combine(
            day,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1_000_000_000,
    )


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, float):
        if isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        if isnan(value):
            return None
    if hasattr(value, "value"):
        return value.value
    return value


def write_trade_csv(path: Path, trades: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not trades:
        path.write_text("", encoding="utf-8")
        return
    rows: list[dict[str, Any]] = []
    for trade in trades:
        row = {
            key: json_safe(value)
            for key, value in trade.items()
            if key != "feature_details"
        }
        for key, value in trade["feature_details"].items():
            row[f"signal_{key}"] = json_safe(value)
        rows.append(row)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_signal_csv(path: Path, signals: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [json_safe(asdict(signal)) for signal in signals]
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--futures-agg", type=Path, nargs="+", required=True)
    parser.add_argument("--spot-agg", type=Path, nargs="+", required=True)
    parser.add_argument("--metrics", type=Path, nargs="+", required=True)
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=CANDIDATE_DIR / "lcpt_config.json",
    )
    parser.add_argument("--allow-unlisted-week", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    week = args.week_start.isoformat()
    allowed_weeks = set(config.development_weeks) | set(config.validation_weeks)
    if not args.allow_unlisted_week and week not in allowed_weeks:
        parser.error(f"week is not precommitted: {sorted(allowed_weeks)}")

    start_ns = date_to_ns(args.week_start)
    end_ns = start_ns + 7 * NS_PER_DAY

    futures_stream = AggTradeArchiveStream(args.futures_agg)
    futures_minutes = aggregate_minute_bars(futures_stream)
    futures_stats = futures_stream.stats()

    spot_stream = AggTradeArchiveStream(args.spot_agg)
    spot_minutes = aggregate_minute_bars(spot_stream)
    spot_stats = spot_stream.stats()

    open_interest, metrics_stats = load_open_interest_metrics(args.metrics)
    states = build_states(futures_minutes, spot_minutes, open_interest)
    signals = detect_cascade_signals(
        config,
        futures_minutes,
        states,
        start_ns,
        end_ns,
    )

    raw_events: list[dict[str, Any]] = []
    event_sequence = 0

    def emit(**values: Any) -> None:
        nonlocal event_sequence
        event_sequence += 1
        raw_events.append({**values, "_sequence": event_sequence})

    replay = LcptReplay(
        config,
        futures_minutes,
        signals,
        emit,
        start_ns,
        end_ns,
    ).run([str(path) for path in args.futures_agg])

    data_stats = {
        "futures_aggregate_trades": futures_stats.to_dict(),
        "spot_aggregate_trades": spot_stats.to_dict(),
        "open_interest_metrics": metrics_stats.to_dict(),
    }
    metrics = build_metrics(
        config,
        replay,
        week_start=week,
        week_end=(args.week_start + timedelta(days=7)).isoformat(),
        data_stats=data_stats,
    )
    trade_rows = metrics.pop("trades_detail")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    write_trade_csv(output / "trades.csv", trade_rows)
    write_signal_csv(output / "signals.csv", signals)

    events: list[ResearchEvent] = []
    for values in sorted(
        raw_events,
        key=lambda row: (row["observed_time_ns"], row["_sequence"]),
    ):
        events.append(
            ResearchEvent(
                scenario_id=values["scenario_id"],
                instrument_id=config.futures_instrument_id,
                event_type=values["event_type"],
                event_time_ns=values["event_time_ns"],
                observed_time_ns=values["observed_time_ns"],
                previous_state=values["previous_state"],
                next_state=values["next_state"],
                reason_code=values["reason_code"],
                reference_price=(
                    None
                    if values["reference_price"] is None
                    else format(float(values["reference_price"]), ".12g")
                ),
                details=json_safe(values["details"]),
            ),
        )
    validate_events(events)
    write_events(output / "scenario_events.jsonl", events)

    metrics.update(
        {
            "label": args.label,
            "config": json_safe(asdict(config)),
            "frozen_development_weeks": list(config.development_weeks),
            "frozen_validation_weeks": list(config.validation_weeks),
            "scenario_event_count": len(events),
            "signal_count": len(signals),
        },
    )
    safe_metrics = json_safe(metrics)
    write_json_atomic(output / "metrics.json", safe_metrics)

    all_hashes = (
        list(futures_stats.sha256)
        + list(spot_stats.sha256)
        + list(metrics_stats.sha256)
    )
    run_manifest = create_run_manifest(
        run_id=args.label,
        candidate=config.candidate,
        config_path=args.config,
        extra={
            "week_start_utc": week,
            "week_end_utc": (args.week_start + timedelta(days=7)).isoformat(),
            "futures_files": list(futures_stats.files),
            "spot_files": list(spot_stats.files),
            "metrics_files": list(metrics_stats.files),
            "data_sha256": all_hashes,
            "futures_event_rows": futures_stats.rows,
            "spot_event_rows": spot_stats.rows,
            "metrics_rows": metrics_stats.rows,
            "causal_contract": (
                "5m ignition -> 5m continuation/OI contraction -> "
                "1m invalidation buffer -> first later futures aggregate trade"
            ),
            "mark_to_market_nav": True,
            "single_slot": True,
        },
    )
    write_json_atomic(output / "run.json", json_safe(run_manifest))

    summary_keys = (
        "week_start_utc",
        "trades",
        "win_rate",
        "mean_net_r",
        "net_return",
        "daily_geometric_growth",
        "max_drawdown",
        "target_met",
        "gate_passed",
    )
    print(
        json.dumps(
            {key: safe_metrics[key] for key in summary_keys},
            indent=2,
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
