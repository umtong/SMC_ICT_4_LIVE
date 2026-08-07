#!/usr/bin/env python3
"""Sequential, process-isolated NautilusTrader evaluation pipeline for Candidate 05."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import subprocess
import sys
import traceback
from typing import Any

from timestamp_contract import install as install_timestamp_contract
from wrangler_contract import install as install_wrangler_contract
from positioning_contract import install as install_positioning_contract
from basis_contract import install as install_basis_contract

install_timestamp_contract()
install_wrangler_contract()
install_positioning_contract()
install_basis_contract()

from backtest import run_backtest
from smc_ict_4.manifest import write_json_atomic


def run_backtest_isolated(
    *,
    config_path: Path,
    build_start: date,
    build_end: date,
    evaluation_start: date,
    evaluation_end: date,
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    """Run one Nautilus BacktestNode in a fresh process.

    NautilusTrader 1.230.0 initializes its Rust logging system once per process.
    A fresh process for each gate prevents a second BacktestNode from attempting
    to install another global logger. This changes no data, strategy, execution,
    accounting, or acceptance rule.
    """
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "stage",
        "--config",
        str(config_path.resolve()),
        "--build-start",
        str(build_start),
        "--build-end",
        str(build_end),
        "--evaluation-start",
        str(evaluation_start),
        "--evaluation-end",
        str(evaluation_end),
        "--cache",
        str(cache.resolve()),
        "--output",
        str(output.resolve()),
    ]
    subprocess.run(command, check=True)
    metrics_path = output.resolve() / "metrics.json"
    if not metrics_path.exists():
        raise RuntimeError(f"isolated Nautilus stage did not produce {metrics_path}")
    return json.loads(metrics_path.read_text(encoding="utf-8"))


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    weeks = json.loads(args.weeks.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache = args.cache.resolve()
    summary: dict[str, Any] = {
        "candidate": "candidate-05-positioning-reset-reversal",
        "engine": "NautilusTrader BacktestNode",
        "process_isolation": "one Nautilus BacktestNode per child process",
        "week_selection": weeks["selection"],
        "stages": [],
        "stopped_after": None,
        "long_evaluation": None,
    }

    selected_weeks = weeks["weeks"][: args.max_weeks]
    for index, item in enumerate(selected_weeks, start=1):
        evaluation_start = date.fromisoformat(item["start"])
        evaluation_end = date.fromisoformat(item["end"])
        build_start = evaluation_start - timedelta(days=int(weeks["warmup_days"]))
        stage_output = output / f"week-{index}"
        try:
            metrics = run_backtest_isolated(
                config_path=args.config,
                build_start=build_start,
                build_end=evaluation_end,
                evaluation_start=evaluation_start,
                evaluation_end=evaluation_end,
                cache=cache,
                output=stage_output,
            )
        except Exception as exc:
            (stage_output / "errors.log").parent.mkdir(parents=True, exist_ok=True)
            (stage_output / "errors.log").write_text(traceback.format_exc(), encoding="utf-8")
            summary["stages"].append(
                {
                    "stage": f"week-{index}",
                    "start": str(evaluation_start),
                    "end": str(evaluation_end),
                    "gate_pass": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            summary["stopped_after"] = f"week-{index}-error"
            write_json_atomic(output / "pipeline_summary.json", summary)
            raise

        summary["stages"].append(
            {
                "stage": f"week-{index}",
                "start": str(evaluation_start),
                "end": str(evaluation_end),
                "gate_pass": bool(metrics["gate_pass"]),
                "geometric_daily_growth": metrics["geometric_daily_growth"],
                "total_return": metrics["total_return"],
                "max_drawdown": metrics["max_drawdown"],
                "trades": metrics["trades"],
                "wins": metrics["wins"],
                "win_rate": metrics["win_rate"],
            },
        )
        write_json_atomic(output / "pipeline_summary.json", summary)
        if not metrics["gate_pass"]:
            summary["stopped_after"] = f"week-{index}-gate-fail"
            write_json_atomic(output / "pipeline_summary.json", summary)
            return summary

    all_three_passed = len(summary["stages"]) == 3 and all(stage["gate_pass"] for stage in summary["stages"])
    if args.run_long and all_three_passed:
        long_spec = weeks["long_evaluation"]
        evaluation_start = date.fromisoformat(long_spec["start"])
        evaluation_end = date.fromisoformat(long_spec["end"])
        build_start = evaluation_start - timedelta(days=int(weeks["warmup_days"]))
        long_output = output / "long"
        metrics = run_backtest_isolated(
            config_path=args.config,
            build_start=build_start,
            build_end=evaluation_end,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            cache=cache,
            output=long_output,
        )
        summary["long_evaluation"] = {
            "start": str(evaluation_start),
            "end": str(evaluation_end),
            "gate_pass": bool(metrics["gate_pass"]),
            "geometric_daily_growth": metrics["geometric_daily_growth"],
            "total_return": metrics["total_return"],
            "max_drawdown": metrics["max_drawdown"],
            "trades": metrics["trades"],
            "wins": metrics["wins"],
            "win_rate": metrics["win_rate"],
        }
        summary["stopped_after"] = "long-complete"
    elif all_three_passed:
        summary["stopped_after"] = "week-3-pass-long-not-requested"
    else:
        summary["stopped_after"] = f"week-{len(summary['stages'])}-complete"
    write_json_atomic(output / "pipeline_summary.json", summary)
    return summary


def add_stage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    pipeline = subparsers.add_parser("pipeline")
    pipeline.add_argument("--config", type=Path, required=True)
    pipeline.add_argument("--weeks", type=Path, required=True)
    pipeline.add_argument("--cache", type=Path, required=True)
    pipeline.add_argument("--output", type=Path, required=True)
    pipeline.add_argument("--max-weeks", type=int, default=3, choices=(1, 2, 3))
    pipeline.add_argument("--run-long", action="store_true")

    stage = subparsers.add_parser("stage")
    add_stage_arguments(stage)

    args = parser.parse_args()
    if args.command == "pipeline":
        result = run_pipeline(args)
    else:
        result = run_backtest(
            config_path=args.config,
            build_start=date.fromisoformat(args.build_start),
            build_end=date.fromisoformat(args.build_end),
            evaluation_start=date.fromisoformat(args.evaluation_start),
            evaluation_end=date.fromisoformat(args.evaluation_end),
            cache=args.cache,
            output=args.output,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
