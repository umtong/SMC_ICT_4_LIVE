#!/usr/bin/env python3
"""Nautilus W1 tournament for parent-accepted multiclock retests."""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from pathlib import Path
from typing import Any

import backtest as base
from multiclock_ensemble_scenario import (
    build_ensemble_signals,
    discover_ensemble,
)
from parent_acceptance_ensemble_scenario import (
    build_parent_acceptance_ensemble_signals,
    discover_parent_acceptance_ensemble,
)
import run_local_liquidity_sweep_mss_retest as local
from smc_ict_4.manifest import write_json_atomic


def _worker(
    args: argparse.Namespace,
    config_path: Path,
    variant: str,
) -> None:
    original_discover = local.discover_structural_signals
    original_builder = local.build_causal_signals
    if variant == "baseline_parent_acceptance":
        local.discover_structural_signals = discover_parent_acceptance_ensemble
        local.build_causal_signals = build_parent_acceptance_ensemble_signals
        route = (
            "15S sweep -> 5S retest -> completed parent 15S price/flow acceptance, "
            "or full 15S retest"
        )
    else:
        local.discover_structural_signals = discover_ensemble
        local.build_causal_signals = build_ensemble_signals
        route = "15S sweep -> first completed 5S or 15S retest"
    try:
        metrics = local._run_variant(
            args=args,
            config_path=config_path,
            variant=variant,
            require_retest=True,
        )
        metrics["execution_contract"].update(
            {
                "selected_route": route,
                "source_timeframe": "15S",
                "episode_reuse": False,
                "single_pending_or_open_slot": True,
            }
        )
        write_json_atomic(
            args.output.resolve() / variant / "metrics.json",
            base._json_safe(metrics),
        )
    finally:
        local.discover_structural_signals = original_discover
        local.build_causal_signals = original_builder
        engine = getattr(local, "_EmptySignalSafeBacktestEngine", None)
        if engine is not None:
            engine.delegate_type = None


def _isolated(
    *,
    args: argparse.Namespace,
    config_path: Path,
    variant: str,
) -> dict[str, Any]:
    process = mp.get_context("spawn").Process(
        target=_worker,
        args=(args, config_path, variant),
    )
    process.start()
    process.join()
    if process.exitcode != 0:
        raise RuntimeError(f"parent-acceptance variant failed: {variant} exit={process.exitcode}")
    path = args.output.resolve() / variant / "metrics.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid parent-acceptance metrics: {path}")
    return payload


def run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config["max_hold_minutes"] = 30
    config_path = output / "frozen_config.json"
    write_json_atomic(config_path, config)

    baseline = _isolated(
        args=args,
        config_path=config_path,
        variant="baseline_parent_acceptance",
    )
    ablation = _isolated(
        args=args,
        config_path=config_path,
        variant="ablation_first_retest_only",
    )
    variants = {
        "baseline_parent_acceptance": local._compact(baseline),
        "ablation_first_retest_only": local._compact(ablation),
    }
    baseline_passed = bool(
        (variants["baseline_parent_acceptance"].get("weekly_gate") or {}).get(
            "passed"
        )
    )
    summary = {
        "candidate": "candidate-07",
        "family": "parent_accepted_multiclock_retest",
        "stage": "week-1",
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "source_commit_expected": args.source_commit,
        "engine": "NautilusTrader BacktestEngine",
        "engine_process_isolation": True,
        "risk_fraction": config["risk_fraction"],
        "maximum_hold_minutes": config["max_hold_minutes"],
        "controlled_variable": (
            "require completed parent 15S price-and-flow acceptance after a 5S retest"
        ),
        "variants": variants,
        "selected_variant": "baseline_parent_acceptance" if baseline_passed else None,
        "eligible_for_frozen_week_2": baseline_passed,
        "interpretation": (
            "WEEK_1_GATE_PASSED"
            if baseline_passed
            else "PARENT_ACCEPTANCE_FAILED_WEEK_1"
        ),
    }
    write_json_atomic(output / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main() -> int:
    return run(local.build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
