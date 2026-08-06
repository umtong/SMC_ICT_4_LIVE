"""Reproducible research runner for candidate 10."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import sys

from candidate import MachineParams
from candidate import reproducible_weeks
from candidate import run_backtest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("gate", "three-weeks", "single"), default="gate")
    parser.add_argument("--week", help="ISO Monday for --phase single")
    parser.add_argument("--output", default="artifacts/candidate-10")
    parser.add_argument("--data-root", default="artifacts/candidate-10-data")
    return parser.parse_args()


def variants() -> dict[str, MachineParams]:
    full = MachineParams()
    # Required one-variable ablation: remove acceptance while keeping every
    # threshold, risk rule, target rule and execution assumption unchanged.
    rejection_only = replace(full, enable_acceptance=False)
    return {"full": full, "ablation-no-acceptance": rejection_only}


def main() -> int:
    args = parse_args()
    selected = reproducible_weeks()
    if args.phase == "gate":
        weeks = selected[:1]
    elif args.phase == "three-weeks":
        weeks = selected
    else:
        if not args.week:
            raise SystemExit("--week is required for --phase single")
        weeks = [date.fromisoformat(args.week)]

    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    selection = {
        "seed": 20260806,
        "population": "all Mondays from 2022-01-03 through 2024-12-23 inclusive",
        "selected_weeks": [item.isoformat() for item in selected],
        "phase": args.phase,
        "executed_weeks": [item.isoformat() for item in weeks],
    }
    (output_root / "week_selection.json").write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    results: list[dict[str, object]] = []
    for week in weeks:
        for variant, params in variants().items():
            destination = output_root / args.phase / week.isoformat() / variant
            metrics = run_backtest(
                week_start=week,
                variant=variant,
                params=params,
                output_dir=destination,
                data_root=Path(args.data_root) / week.isoformat(),
            )
            results.append(metrics)
            print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True), flush=True)

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
