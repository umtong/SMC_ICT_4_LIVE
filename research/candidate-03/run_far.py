#!/usr/bin/env python3
"""Run the frozen FAR candidate on one precommitted BTC validation week."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any

CANDIDATE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CANDIDATE_DIR.parents[1]
SRC = REPO_ROOT / "src"
if SRC.is_dir():
    sys.path.insert(0, str(SRC))
sys.path.insert(0, str(CANDIDATE_DIR))

from far_data import AggTradeArchiveStream
from far_model import FarConfig
from far_replay import FarReplay
from smc_ict_4.contracts import ResearchEvent
from smc_ict_4.event_log import validate_events, write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic


def load_config(path: Path) -> FarConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    allowed = {item.name for item in fields(FarConfig)}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown FAR config fields: {unknown}")
    if isinstance(payload.get("validation_weeks"), list):
        payload["validation_weeks"] = tuple(payload["validation_weeks"])
    config = FarConfig(**payload)
    config.validate()
    return config


def to_ns(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1e9)


def json_safe(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and value == float("inf"):
        return "Infinity"
    return value


def write_trades(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    flattened: list[dict[str, Any]] = []
    for row in rows:
        normalized = {key: json_safe(value) for key, value in row.items() if key != "feature_details"}
        for key, value in row["feature_details"].items():
            normalized[f"signal_{key}"] = json_safe(value)
        flattened.append(normalized)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(flattened[0]))
        writer.writeheader()
        writer.writerows(flattened)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, nargs="+", required=True)
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CANDIDATE_DIR / "far_config.json")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.week_start.isoformat() not in config.validation_weeks:
        parser.error(f"week is not precommitted: {config.validation_weeks}")
    start_ns = to_ns(args.week_start)
    end_ns = to_ns(args.week_start + timedelta(days=7))
    events: list[ResearchEvent] = []

    def emit(**values: Any) -> None:
        events.append(
            ResearchEvent(
                scenario_id=values["scenario_id"],
                instrument_id=config.instrument_id,
                event_type=values["event_type"],
                event_time_ns=values["event_time_ns"],
                observed_time_ns=values["observed_time_ns"],
                previous_state=values["previous_state"],
                next_state=values["next_state"],
                reason_code=values["reason_code"],
                reference_price=(
                    None if values["reference_price"] is None else format(values["reference_price"], ".12g")
                ),
                details=values["details"],
            )
        )

    stream = AggTradeArchiveStream(args.data)
    metrics = FarReplay(config, emit).run(stream, start_ns, end_ns)
    trade_rows = metrics.pop("trades_detail")
    data_stats = asdict(stream.stats())
    metrics.update(
        {
            "label": args.label,
            "week_start_utc": args.week_start.isoformat(),
            "week_end_utc": (args.week_start + timedelta(days=7)).isoformat(),
            "data_stats": data_stats,
            "frozen_validation_weeks": list(config.validation_weeks),
            "config": asdict(config),
        }
    )
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    write_trades(output / "trades.csv", trade_rows)
    validate_events(events)
    write_events(output / "scenario_events.jsonl", events)
    write_json_atomic(output / "metrics.json", json_safe(metrics))
    run_manifest = create_run_manifest(
        run_id=args.label,
        candidate=config.candidate,
        config_path=args.config,
        extra={
            "week_start_utc": args.week_start.isoformat(),
            "week_end_utc": (args.week_start + timedelta(days=7)).isoformat(),
            "data_files": data_stats["files"],
            "data_sha256": data_stats["sha256"],
            "event_rows": data_stats["rows"],
            "causal_timestamp_contract": "minute close -> first later aggregate trade",
        },
    )
    write_json_atomic(output / "run.json", run_manifest)
    summary_keys = (
        "week_start_utc",
        "trades",
        "win_rate",
        "mean_net_r",
        "net_return",
        "daily_geometric_growth",
        "max_drawdown",
        "target_met",
    )
    print(json.dumps({key: json_safe(metrics[key]) for key in summary_keys}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
