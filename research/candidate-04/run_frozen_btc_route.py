#!/usr/bin/env python3
"""Compile and execute one frozen candidate route through NautilusTrader.

This is process orchestration only. It invokes an existing completed-data
compiler, the explicit scenario-family filter, the exact-target NautilusTrader
runner and the established evidence summarizer. It never matches orders or
calculates fills, positions, PnL or NAV itself.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def run(command: list[str], *, env: dict[str, str], log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        subprocess.run(
            command,
            check=True,
            cwd=ROOT.parent.parent,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--family",
        required=True,
        choices=("v33", "v34", "v35", "v36", "v37", "v38", "v41", "v43"),
    )
    parser.add_argument(
        "--route",
        required=True,
        choices=("full", "continuation", "reversal"),
    )
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--impact-config", type=Path, required=True)
    parser.add_argument("--router-config", type=Path, required=True)
    parser.add_argument("--execution-config", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--build-start", type=date.fromisoformat, required=True)
    parser.add_argument("--build-end", type=date.fromisoformat, required=True)
    parser.add_argument("--evaluation-start", type=date.fromisoformat, required=True)
    parser.add_argument("--evaluation-end", type=date.fromisoformat, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-trades", type=int, default=4)
    parser.add_argument("--min-active-days", type=int, default=3)
    parser.add_argument("--min-win-rate", type=float, default=0.55)
    parser.add_argument("--min-geometric-daily", type=float, default=0.01)
    args = parser.parse_args()

    if args.evaluation_start < args.build_start:
        raise SystemExit("evaluation starts before build range")
    if args.evaluation_end > args.build_end:
        raise SystemExit("evaluation ends after build range")
    for path in (
        args.compiler,
        args.base_config,
        args.impact_config,
        args.router_config,
        args.execution_config,
    ):
        if not path.exists():
            raise SystemExit(f"missing required input: {path}")
    if not (args.rich_dir / "data_manifest.json").exists():
        raise SystemExit(f"rich data manifest missing: {args.rich_dir}")

    args.output.mkdir(parents=True, exist_ok=True)
    all_signals = args.output / "all_signals"
    signals = args.output / "signals"
    nautilus = args.output / "nautilus"
    env = dict(os.environ)
    env.update(
        {
            "C04_BUILD_START": str(args.build_start),
            "C04_BUILD_END": str(args.build_end),
            "C04_EVALUATION_START": str(args.evaluation_start),
            "C04_EVALUATION_END": str(args.evaluation_end),
        }
    )

    run(
        [
            sys.executable,
            str(args.compiler),
            "--base-config",
            str(args.base_config),
            "--impact-config",
            str(args.impact_config),
            "--router-config",
            str(args.router_config),
            "--rich-dir",
            str(args.rich_dir),
            "--kline-dir",
            str(args.cache / "raw"),
            "--evaluation-start",
            str(args.evaluation_start),
            "--evaluation-end",
            str(args.evaluation_end),
            "--output",
            str(all_signals),
            "--download-klines",
        ],
        env=env,
        log=args.output / "compiler_console.log",
    )
    run(
        [
            sys.executable,
            str(ROOT / "filter_candidate_signals.py"),
            "--input",
            str(all_signals / "signals.json"),
            "--output-dir",
            str(signals),
            "--family",
            args.family,
            "--route",
            args.route,
        ],
        env=env,
        log=args.output / "filter_console.log",
    )
    execution_env = dict(env)
    execution_env["C04_SIGNALS_PATH"] = str(
        (signals / "signals.json").resolve()
    )
    run(
        [
            sys.executable,
            str(ROOT / "nt_backtest_v31_exact_causal_target.py"),
            "--config",
            str(args.execution_config),
            "--build-start",
            str(args.build_start),
            "--build-end",
            str(args.build_end),
            "--evaluation-start",
            str(args.evaluation_start),
            "--evaluation-end",
            str(args.evaluation_end),
            "--cache",
            str(args.cache),
            "--output",
            str(nautilus),
        ],
        env=execution_env,
        log=args.output / "nautilus_console.log",
    )
    run(
        [
            sys.executable,
            str(ROOT / "summarize_candidate_week.py"),
            "--root",
            str(args.output),
            "--candidate",
            args.candidate,
            "--stage",
            args.stage,
            "--min-trades",
            str(args.min_trades),
            "--min-active-days",
            str(args.min_active_days),
            "--min-win-rate",
            str(args.min_win_rate),
            "--min-geometric-daily",
            str(args.min_geometric_daily),
            "--output",
            str(args.output / "summary.json"),
        ],
        env=env,
        log=args.output / "summary_console.log",
    )
    summary = json.loads((args.output / "summary.json").read_text())
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
