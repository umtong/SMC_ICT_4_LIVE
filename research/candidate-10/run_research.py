"""Reproducible candidate 10 v4 official-L1 controlled runner."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import c10_flow_research as _flow_research_module
import c10_flow_v4 as _v4_module
from c10_flow_evidence_fix import EvidenceValidatedParentProtectedStrategy
from c10_flow_l1_research import run_l1_flow_backtest
from c10_flow_model import FlowParams
from c10_flow_precision_fix import reproducible_weeks

VARIANT_NAMES = ("full", "ablation-price-only-acceptance")


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
    parser.add_argument("--variant", choices=VARIANT_NAMES, help=argparse.SUPPRESS)
    return parser.parse_args()


def variants() -> dict[str, tuple[FlowParams, bool]]:
    params = FlowParams()
    return {
        "full": (params, True),
        "ablation-price-only-acceptance": (params, False),
    }


def _worker(args: argparse.Namespace, output_root: Path) -> int:
    if not args.week or not args.variant:
        raise SystemExit("isolated worker requires --week and --variant")
    week = date.fromisoformat(args.week)
    params, require_flow = variants()[args.variant]
    destination = output_root / args.phase / week.isoformat() / args.variant

    # Controlled implementation rerun:
    # - v4 state machine, parameters, seed, costs, risk, signal prices and the
    #   one-variable order-flow ablation are unchanged.
    # - only the execution data path gains the official latest-known Binance
    #   best bid/ask before each aggregate trade.
    previous_strategy = _flow_research_module.FlowCandidate10Strategy
    previous_backtest = _v4_module._run_flow_backtest
    _flow_research_module.FlowCandidate10Strategy = (
        EvidenceValidatedParentProtectedStrategy
    )
    _v4_module._run_flow_backtest = run_l1_flow_backtest
    try:
        metrics = _v4_module.run_v4_backtest(
            require_acceptance_order_flow=require_flow,
            week_start=week,
            variant=args.variant,
            params=params,
            output_dir=destination,
            data_root=Path(args.data_root) / week.isoformat(),
        )
    finally:
        _v4_module._run_flow_backtest = previous_backtest
        _flow_research_module.FlowCandidate10Strategy = previous_strategy

    metrics["execution_lifecycle"] = (
        "OFFICIAL_CAUSAL_L1_PARENT_ONLY_CANCEL_REMAINDER_"
        "PER_FILL_REDUCE_ONLY_PROTECTION"
    )
    metrics["implementation_control"] = {
        "strategy_logic_changed": False,
        "parameters_changed": False,
        "risk_changed": False,
        "costs_changed": False,
        "seed_changed": False,
        "week_changed": False,
        "execution_data_change": (
            "TradeTick-only synthetic BBO replaced by latest official bookTicker "
            "event_time <= aggregate-trade transact_time"
        ),
    }
    metrics_path = destination / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True), flush=True)
    return 0


def _run_isolated(
    *,
    args: argparse.Namespace,
    week: date,
    variant: str,
) -> dict[str, object]:
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
            f"isolated {variant} worker failed with exit code "
            f"{completed.returncode}",
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
        "population": (
            "all Mondays from 2022-01-03 through 2024-12-23 inclusive"
        ),
        "selected_weeks": [item.isoformat() for item in selected],
        "phase": args.phase,
        "executed_weeks": [item.isoformat() for item in weeks],
        "engine_process_isolation": True,
        "candidate_generation": "v4-efficient-flow-acceptance-continuation",
        "execution_generation": "official-causal-l1-streaming-control",
        "variants": list(VARIANT_NAMES),
        "ablation_contract": (
            "both variants retain fast-range price acceptance, price efficiency, "
            "accepted-boundary entry, macro target, stop, cost, risk and seed; "
            "only same-side executed aggressor-flow confirmation is removed"
        ),
        "implementation_control": (
            "same BTC week, strategy, signal/entry/stop/target, risk, fees, seed "
            "and fill model; add only latest official Binance bookTicker quote "
            "whose event_time is no later than each aggregate-trade transact_time; "
            "stream bounded daily batches through NautilusTrader"
        ),
    }
    (output_root / "week_selection.json").write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    results: list[dict[str, object]] = []
    for week in weeks:
        for variant in variants():
            results.append(
                _run_isolated(args=args, week=week, variant=variant),
            )
    full_results = [item for item in results if item["variant"] == "full"]
    summary = {
        "selection": selection,
        "results": results,
        "all_full_target_pass": bool(full_results)
        and all(bool(item["target_pass"]) for item in full_results),
    }
    summary_path = output_root / args.phase / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
