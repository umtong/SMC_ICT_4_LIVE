"""Process-isolated v22 gate: external session targets vs nearest-any targets."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

from v20_impact_control import install as install_impact_control

install_impact_control()

import c10_liquidation_research as research
from c10_liquidation_state import LiquidationParams

VARIANTS = ("full-external-session-target", "ablation-nearest-any-pool")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("gate", "three-weeks", "single", "auto"), default="auto")
    parser.add_argument("--week")
    parser.add_argument("--output", default="artifacts/candidate-10-v22")
    parser.add_argument("--data-root", default="artifacts/candidate-10-v22-data")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--variant", choices=VARIANTS, help=argparse.SUPPRESS)
    return parser.parse_args()


def _worker(args: argparse.Namespace, output_root: Path) -> int:
    if not args.week or not args.variant:
        raise SystemExit("worker requires --week and --variant")
    if args.variant == "full-external-session-target":
        from c10_v22_install import install_external_target

        install_external_target()
    week = date.fromisoformat(args.week)
    destination = output_root / args.phase / week.isoformat() / args.variant
    metrics = research.run_liquidation_backtest(
        week_start=week,
        variant=args.variant,
        params=LiquidationParams(require_oi_state=True),
        output_dir=destination,
        data_root=Path(args.data_root) / week.isoformat(),
    )
    metrics["candidate_generation"] = "v22-external-session-target-hierarchy"
    metrics["target_hierarchy"] = (
        "EXTERNAL_FUNDING_SESSION_ONLY"
        if args.variant == "full-external-session-target"
        else "NEAREST_ANY_ACTIVE_POOL"
    )
    metrics["exact_ablation"] = (
        "remove only external target hierarchy; detector, OI, entry, stop, costs, "
        "size-dependent impact, seed and 3% risk remain fixed"
    )
    research.write_json_atomic(destination / "metrics.json", metrics)
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True), flush=True)
    return 0


def _run_isolated(*, args: argparse.Namespace, week: date, variant: str) -> dict[str, object]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--phase", args.phase,
        "--week", week.isoformat(),
        "--variant", variant,
        "--output", args.output,
        "--data-root", args.data_root,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(
            f"isolated worker failed: week={week} variant={variant} exit={completed.returncode}",
        )
    marker = "RESULT_JSON="
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(marker):
            return json.loads(line[len(marker):])
    raise RuntimeError("worker returned no RESULT_JSON")


def _write_summary(
    output_root: Path,
    phase: str,
    selection: dict[str, object],
    results: list[dict[str, object]],
) -> None:
    full = [item for item in results if item.get("variant") == VARIANTS[0]]
    target = output_root / phase / "summary.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "selection": selection,
                "results": results,
                "all_full_target_pass": bool(full)
                and all(bool(item.get("target_pass")) for item in full),
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    if args.worker:
        return _worker(args, output_root)

    selected = research.reproducible_weeks()
    if args.phase == "gate":
        weeks = selected[:1]
    elif args.phase == "three-weeks":
        weeks = selected
    elif args.phase == "single":
        if not args.week:
            raise SystemExit("--week required for --phase single")
        weeks = [date.fromisoformat(args.week)]
    else:
        weeks = selected[:1]

    selection: dict[str, object] = {
        "seed": 20260806,
        "population": "all Mondays from 2022-01-03 through 2024-12-23 inclusive",
        "selected_weeks": [item.isoformat() for item in selected],
        "executed_weeks": [item.isoformat() for item in weeks],
        "phase": args.phase,
        "candidate_generation": "v22-external-session-target-hierarchy",
        "variants": list(VARIANTS),
        "ablation_contract": (
            "only target pool class changes: completed 8h funding-session external "
            "liquidity versus nearest active pool; OI, event detection, second-bar "
            "confirmation, next-tick entry, stop, costs, size, seed and risk are fixed"
        ),
        "process_isolation": True,
    }
    (output_root / "week_selection.json").write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    results: list[dict[str, object]] = []
    for week in weeks:
        for variant in VARIANTS:
            results.append(_run_isolated(args=args, week=week, variant=variant))
    _write_summary(output_root, args.phase, selection, results)

    if args.phase == "auto":
        gate_full = next(item for item in results if item.get("variant") == VARIANTS[0])
        if bool(gate_full.get("target_pass")):
            later: list[dict[str, object]] = []
            for week in selected[1:]:
                for variant in VARIANTS:
                    later.append(_run_isolated(args=args, week=week, variant=variant))
            selection["executed_weeks"] = [item.isoformat() for item in selected]
            _write_summary(output_root, "three-weeks", selection, [*results, *later])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
