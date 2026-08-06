"""Reproducible research runner for candidate 10."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

from candidate import MachineParams
from candidate import reproducible_weeks
from candidate import run_backtest


VARIANT_NAMES = ("full", "ablation-single-bar-displacement")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("gate", "three-weeks", "single"), default="gate")
    parser.add_argument("--week", help="ISO Monday for --phase single or an isolated worker")
    parser.add_argument("--output", default="artifacts/candidate-10")
    parser.add_argument("--data-root", default="artifacts/candidate-10-data")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--variant",
        choices=VARIANT_NAMES,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def variants() -> dict[str, MachineParams]:
    full = MachineParams()
    # One-variable v2.1 ablation: restore the preceding single-candle
    # displacement certification while keeping pools, costs, targets, risk,
    # entry, expiry and every numerical threshold unchanged.
    single_bar = replace(full, enable_path_displacement=False)
    return {
        "full": full,
        "ablation-single-bar-displacement": single_bar,
    }


def _worker(args: argparse.Namespace, output_root: Path) -> int:
    if not args.week or not args.variant:
        raise SystemExit("isolated worker requires --week and --variant")
    week = date.fromisoformat(args.week)
    params = variants()[args.variant]
    destination = output_root / args.phase / week.isoformat() / args.variant
    metrics = run_backtest(
        week_start=week,
        variant=args.variant,
        params=params,
        output_dir=destination,
        data_root=Path(args.data_root) / week.isoformat(),
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True), flush=True)
    return 0


def _run_isolated(
    *,
    args: argparse.Namespace,
    week: date,
    variant: str,
) -> dict[str, object]:
    # NautilusTrader 1.230 owns a process-global Rust logger. A fresh process
    # per engine prevents the ablation from mutating or inheriting engine state.
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--phase",
        args.phase,
        "--week",
        week.isoformat(),
        "--variant",
        variant,
        "--output",
        args.output,
        "--data-root",
        args.data_root,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(
            f"isolated {variant} worker failed with exit code {completed.returncode}",
        )
    marker = "RESULT_JSON="
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(marker):
            return json.loads(line[len(marker) :])
    raise RuntimeError(f"isolated {variant} worker returned no RESULT_JSON")


def main() -> int:
    args = parse_args()
    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    if args.worker:
        return _worker(args, output_root)

    selected = reproducible_weeks()
    if args.phase == "gate":
        weeks = selected[:1]
    elif args.phase == "three-weeks":
        weeks = selected
    else:
        if not args.week:
            raise SystemExit("--week is required for --phase single")
        weeks = [date.fromisoformat(args.week)]

    selection = {
        "seed": 20260806,
        "population": "all Mondays from 2022-01-03 through 2024-12-23 inclusive",
        "selected_weeks": [item.isoformat() for item in selected],
        "phase": args.phase,
        "executed_weeks": [item.isoformat() for item in weeks],
        "engine_process_isolation": True,
        "candidate_generation": "v2.1-efficient-event-path-displacement",
        "variants": list(VARIANT_NAMES),
    }
    (output_root / "week_selection.json").write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    results: list[dict[str, object]] = []
    for week in weeks:
        for variant in variants():
            results.append(_run_isolated(args=args, week=week, variant=variant))

    summary = {
        "selection": selection,
        "results": results,
        "all_full_target_pass": all(
            bool(item["target_pass"]) for item in results if item["variant"] == "full"
        ),
    }
    (output_root / args.phase / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
