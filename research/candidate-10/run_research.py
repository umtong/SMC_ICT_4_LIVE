"""Reproducible candidate 10 v3 research runner."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

from c10_flow_model import FlowParams
from c10_flow_precision_fix import reproducible_weeks
from c10_flow_precision_fix import run_flow_backtest

VARIANT_NAMES = ("full", "ablation-price-only")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("gate", "three-weeks", "single"),
        default="gate",
    )
    parser.add_argument("--week", help="ISO Monday for --phase single or worker")
    parser.add_argument("--output", default="artifacts/candidate-10")
    parser.add_argument("--data-root", default="artifacts/candidate-10-data")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--variant",
        choices=VARIANT_NAMES,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def variants() -> dict[str, FlowParams]:
    full = FlowParams()
    # Exact one-variable ablation: remove executed aggressor-flow direction and
    # magnitude from absorption/repricing certification. Event bars, local range,
    # price response, costs, targets, risk, seed and every threshold stay fixed.
    price_only = replace(full, enable_order_flow=False)
    return {
        "full": full,
        "ablation-price-only": price_only,
    }


def _worker(args: argparse.Namespace, output_root: Path) -> int:
    if not args.week or not args.variant:
        raise SystemExit("isolated worker requires --week and --variant")
    week = date.fromisoformat(args.week)
    params = variants()[args.variant]
    destination = output_root / args.phase / week.isoformat() / args.variant
    metrics = run_flow_backtest(
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
    # NautilusTrader 1.230 owns process-global Rust logging state. Fresh workers
    # preserve exact variant isolation without rebuilding the engine.
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
        "candidate_generation": "v3-event-notional-flow-absorption-repricing",
        "variants": list(VARIANT_NAMES),
        "ablation_contract": (
            "only FlowParams.enable_order_flow changes; all price, execution, "
            "cost, target, risk and selection rules remain identical"
        ),
        "implementation_control": (
            "Binance decimal strings are normalized to instrument Price/Quantity "
            "precision without changing numeric values"
        ),
    }
    (output_root / "week_selection.json").write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    results: list[dict[str, object]] = []
    for week in weeks:
        for variant in variants():
            results.append(_run_isolated(args=args, week=week, variant=variant))

    full_results = [item for item in results if item["variant"] == "full"]
    summary = {
        "selection": selection,
        "results": results,
        "all_full_target_pass": bool(full_results)
        and all(bool(item["target_pass"]) for item in full_results),
    }
    (output_root / args.phase / "summary.json").parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    (output_root / args.phase / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
