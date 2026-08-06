#!/usr/bin/env python3
"""Command line entry point for candidate-07 research and validation."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

from smc_ict_4.manifest import write_json_atomic

from backtest import run_week


def _load_week_plan(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    weeks = payload.get("weeks")
    if not isinstance(weeks, list) or not weeks:
        raise ValueError("week plan must contain a non-empty weeks list")
    return [dict(item) for item in weeks]


def run_pipeline(args: argparse.Namespace) -> int:
    weeks = _load_week_plan(args.week_plan)
    selected = weeks[: args.max_weeks] if args.max_weeks is not None else weeks
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "candidate": "candidate-07",
        "engine": "NautilusTrader BacktestEngine",
        "week_plan": str(args.week_plan),
        "stages": [],
        "stopped_after": None,
        "all_requested_passed": False,
        "long_evaluation_run": False,
    }
    for item in selected:
        stage = str(item["stage"])
        start = date.fromisoformat(str(item["start"]))
        end = date.fromisoformat(str(item["end"]))
        destination = output / stage
        metrics = run_week(
            config_path=args.config,
            stage=stage,
            start=start,
            end=end,
            output=destination,
            cache_root=args.data_root,
        )
        gate_passed = bool(metrics["weekly_gate"]["passed"])
        summary["stages"].append(
            {
                "stage": stage,
                "start": start.isoformat(),
                "end_exclusive": end.isoformat(),
                "metrics_path": str(destination / "metrics.json"),
                "daily_geometric_growth": metrics["daily_geometric_growth"],
                "trades": metrics["trades"],
                "win_rate": metrics["win_rate"],
                "max_drawdown": metrics["max_drawdown"],
                "gate_passed": gate_passed,
                "checks": metrics["weekly_gate"]["checks"],
            }
        )
        write_json_atomic(output / "pipeline_summary.json", summary)
        if not gate_passed and not args.continue_after_failure:
            summary["stopped_after"] = stage
            break
    else:
        summary["all_requested_passed"] = bool(summary["stages"]) and all(
            bool(item["gate_passed"]) for item in summary["stages"]
        )

    if args.run_long and len(summary["stages"]) == len(weeks) and all(
        bool(item["gate_passed"]) for item in summary["stages"]
    ):
        config = json.loads(args.config.read_text(encoding="utf-8"))
        long_values = config["long_evaluation"]
        long_metrics = run_week(
            config_path=args.config,
            stage="long-evaluation",
            start=date.fromisoformat(str(long_values["start"])),
            end=date.fromisoformat(str(long_values["end"])),
            output=output / "long-evaluation",
            cache_root=args.data_root,
        )
        summary["long_evaluation_run"] = True
        summary["long_evaluation"] = {
            "daily_geometric_growth": long_metrics["daily_geometric_growth"],
            "trades": long_metrics["trades"],
            "win_rate": long_metrics["win_rate"],
            "max_drawdown": long_metrics["max_drawdown"],
            "target_met": long_metrics["target_met"],
        }
    write_json_atomic(output / "pipeline_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def run_single(args: argparse.Namespace) -> int:
    metrics = run_week(
        config_path=args.config,
        stage=args.stage,
        start=args.start,
        end=args.end,
        output=args.output.resolve(),
        cache_root=args.data_root,
    )
    print(json.dumps(metrics, indent=2, sort_keys=True, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    pipeline = subparsers.add_parser("pipeline", help="run frozen weekly gates sequentially")
    pipeline.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    pipeline.add_argument("--week-plan", type=Path, default=candidate_dir / "week_plan.json")
    pipeline.add_argument("--output", type=Path, required=True)
    pipeline.add_argument("--data-root", type=Path, default=Path(".research-data/candidate-07"))
    pipeline.add_argument("--max-weeks", type=int, choices=(1, 2, 3))
    pipeline.add_argument("--continue-after-failure", action="store_true")
    pipeline.add_argument("--run-long", action="store_true")
    pipeline.set_defaults(func=run_pipeline)

    single = subparsers.add_parser("week", help="run one explicit bounded interval")
    single.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    single.add_argument("--stage", required=True)
    single.add_argument("--start", type=date.fromisoformat, required=True)
    single.add_argument("--end", type=date.fromisoformat, required=True)
    single.add_argument("--output", type=Path, required=True)
    single.add_argument("--data-root", type=Path, default=Path(".research-data/candidate-07"))
    single.set_defaults(func=run_single)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
