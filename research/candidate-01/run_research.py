#!/usr/bin/env python3
"""Reproducible research entry point for candidate 01."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from math import prod
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for path in (HERE, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core import CandidateConfig  # noqa: E402
from data import load_interval, parse_utc_date, write_download_manifest  # noqa: E402
from nautilus_backtest import ExecutionConfig, run_nautilus_backtest  # noqa: E402
from smc_ict_4.manifest import create_run_manifest, write_json_atomic  # noqa: E402


def load_config(path: Path) -> tuple[CandidateConfig, ExecutionConfig, dict[str, Any], dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    required = {"candidate", "execution", "research"}
    missing = sorted(required - set(raw))
    if missing:
        raise ValueError(f"missing config sections: {missing}")
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    execution = ExecutionConfig.from_mapping(raw["execution"])
    research = dict(raw["research"])
    return candidate, execution, research, raw


def segments_for_suite(suite: str, research: dict[str, Any]) -> list[tuple[str, datetime, datetime]]:
    def week(label: str, value: str) -> tuple[str, datetime, datetime]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7)

    segments: list[tuple[str, datetime, datetime]] = [
        week("discovery", str(research["discovery_week"])),
    ]
    if suite in {"quick", "extended", "full"}:
        segments.extend(
            week(f"confirmation-{index + 1}", value)
            for index, value in enumerate(research["confirmation_weeks"])
        )
    if suite in {"extended", "full"}:
        segments.extend(
            week(f"additional-{index + 1}", value)
            for index, value in enumerate(research.get("additional_random_weeks", []))
        )
    if suite == "full":
        start = parse_utc_date(str(research["long_start"]))
        end = parse_utc_date(str(research["long_end"]))
        segments.append(("long-evaluation", start, end))
    return segments


def aggregate(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    if not metrics:
        raise ValueError("at least one metrics row is required")
    total_days = sum(float(item["calendar_days"]) for item in metrics)
    growth_factor = prod(1.0 + float(item["total_return"]) for item in metrics)
    pooled_geo = (
        growth_factor ** (1.0 / total_days) - 1.0
        if growth_factor > 0.0 and total_days > 0.0
        else -1.0
    )
    random_week_metrics = [item for item in metrics if item["label"] != "long-evaluation"]
    long_metrics = [item for item in metrics if item["label"] == "long-evaluation"]
    all_flat = all(bool(item["ended_flat"]) for item in metrics)
    no_gate_violations = all(
        int(item["one_global_entry_gate_violations"]) == 0 for item in metrics
    )
    no_protective_failures = all(
        int(item.get("protective_order_failures", 0)) == 0 for item in metrics
    )
    no_liquidation_markers = all(
        int(item.get("liquidation_marker_rows", 0)) == 0 for item in metrics
    )
    all_submissions_closed = all(
        int(item["submissions"]) == int(item["closed_positions"]) for item in metrics
    )
    random_weeks_target = bool(random_week_metrics) and all(
        bool(item["target_met"]) for item in random_week_metrics
    )
    random_weeks_have_opportunities = bool(random_week_metrics) and all(
        int(item["closed_positions"]) >= 5 for item in random_week_metrics
    )
    long_target = bool(long_metrics) and all(bool(item["target_met"]) for item in long_metrics)
    long_closed_positions = sum(int(item["closed_positions"]) for item in long_metrics)
    enough_independent_long_trades = long_closed_positions >= 100
    max_drawdown = min(float(item["max_drawdown"]) for item in metrics)
    margin_ratios = [
        float(item["minimum_equity_to_maintenance_margin"])
        for item in metrics
        if item.get("minimum_equity_to_maintenance_margin") is not None
    ]
    minimum_margin_ratio = min(margin_ratios, default=None)
    margin_buffer_preserved = minimum_margin_ratio is not None and minimum_margin_ratio > 1.0
    result = {
        "segments": metrics,
        "total_calendar_days": total_days,
        "pooled_growth_factor": growth_factor,
        "pooled_geometric_mean_daily_return": pooled_geo,
        "target_geometric_mean_daily_return": 0.01,
        "pooled_target_met": pooled_geo >= 0.01,
        "all_random_weeks_target_met": random_weeks_target,
        "random_weeks_have_at_least_five_closed_positions": random_weeks_have_opportunities,
        "long_evaluation_target_met": long_target,
        "long_evaluation_closed_positions": long_closed_positions,
        "long_evaluation_has_at_least_100_closed_positions": enough_independent_long_trades,
        "worst_segment_geometric_daily_return": min(
            float(item["geometric_mean_daily_return"]) for item in metrics
        ),
        "worst_max_drawdown": max_drawdown,
        "drawdown_below_twenty_percent": max_drawdown > -0.20,
        "minimum_equity_to_maintenance_margin": minimum_margin_ratio,
        "margin_buffer_preserved": margin_buffer_preserved,
        "total_submissions": sum(int(item["submissions"]) for item in metrics),
        "all_submissions_closed": all_submissions_closed,
        "all_runs_ended_flat": all_flat,
        "one_global_entry_gate_preserved": no_gate_violations,
        "no_protective_order_failures": no_protective_failures,
        "no_liquidation_markers": no_liquidation_markers,
    }
    result["candidate_success"] = bool(
        long_target
        and random_weeks_target
        and random_weeks_have_opportunities
        and enough_independent_long_trades
        and pooled_geo >= 0.01
        and max_drawdown > -0.20
        and margin_buffer_preserved
        and all_submissions_closed
        and all_flat
        and no_gate_violations
        and no_protective_failures
        and no_liquidation_markers
    )
    return result


def run(args: argparse.Namespace) -> int:
    config_path = args.config.resolve()
    candidate, execution, research, raw = load_config(config_path)
    output_root = args.output.resolve()
    cache_dir = args.cache.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    if args.start or args.end:
        if not args.start or not args.end:
            raise ValueError("--start and --end must be supplied together")
        segments = [
            (
                args.label or "custom",
                parse_utc_date(args.start),
                parse_utc_date(args.end),
            ),
        ]
    else:
        segments = segments_for_suite(args.suite, research)

    metric_rows: list[dict[str, Any]] = []
    for label, start, end in segments:
        destination = output_root / label
        frame, records = load_interval(
            symbol=str(research.get("symbol", "BTCUSDT")),
            start=start,
            end=end,
            cache_dir=cache_dir,
            warmup_minutes=int(research.get("warmup_minutes", candidate.range_minutes + candidate.min_history)),
        )
        data_manifest = write_download_manifest(destination / "data_manifest.json", records)
        evidence = run_nautilus_backtest(
            label=label,
            frame=frame,
            evaluation_start=start,
            evaluation_end=end,
            candidate=candidate,
            execution=execution,
            output_dir=destination,
        )
        metric_rows.append(evidence.metrics)
        run_id = f"candidate-01-{label}-{start.date().isoformat()}"
        write_json_atomic(
            destination / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate="candidate-01-causal-liquidity-auction",
                config_path=config_path,
                data_manifest_path=data_manifest,
                extra={
                    "label": label,
                    "evaluation_start_utc": start.isoformat(),
                    "evaluation_end_utc": end.isoformat(),
                    "candidate_config": candidate.to_dict(),
                    "execution_config": raw["execution"],
                    "seed": research.get("seed"),
                },
            ),
        )
        print(
            json.dumps(
                {
                    "label": label,
                    "geo_daily": evidence.metrics["geometric_mean_daily_return"],
                    "total_return": evidence.metrics["total_return"],
                    "max_drawdown": evidence.metrics["max_drawdown"],
                    "trades": evidence.metrics["submissions"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    aggregate_metrics = aggregate(metric_rows)
    write_json_atomic(output_root / "aggregate_metrics.json", aggregate_metrics)
    write_json_atomic(
        output_root / "run.json",
        create_run_manifest(
            run_id=f"candidate-01-{args.suite}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            candidate="candidate-01-causal-liquidity-auction",
            config_path=config_path,
            extra={
                "suite": args.suite,
                "segment_labels": [label for label, _, _ in segments],
                "candidate_success": aggregate_metrics["candidate_success"],
            },
        ),
    )
    print(json.dumps(aggregate_metrics, indent=2, sort_keys=True), flush=True)
    return 0 if aggregate_metrics["candidate_success"] or args.suite != "full" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01")
    parser.add_argument("--suite", choices=("discovery", "quick", "extended", "full"), default="discovery")
    parser.add_argument("--start", help="custom UTC start date, YYYY-MM-DD")
    parser.add_argument("--end", help="custom UTC end date, YYYY-MM-DD")
    parser.add_argument("--label", help="custom segment label")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
