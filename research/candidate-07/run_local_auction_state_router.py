#!/usr/bin/env python3
"""Nautilus W1 tournament for local rejection and acceptance auction states."""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from pathlib import Path
from typing import Any

import backtest as base
from local_auction_state_scenario import (
    build_auction_state_signals,
    discover_auction_state,
)
from nested_liquidity_sweep_scenario import (
    build_causal_signals as build_rejection_signals,
    discover as discover_rejection,
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
    if variant == "baseline_auction_state_router":
        local.discover_structural_signals = discover_auction_state
        local.build_causal_signals = build_auction_state_signals
        route = (
            "15S first touch -> rejection reclaim/MSS/retest or accepted break/"
            "source-level retest -> nearest causal liquidity"
        )
    else:
        local.discover_structural_signals = (
            lambda *, config, bundle, start, end, require_retest: discover_rejection(
                config=config,
                bundle=bundle,
                start=start,
                end=end,
                require_retest=require_retest,
                include_higher_sources=False,
            )
        )
        local.build_causal_signals = build_rejection_signals
        route = "15S first-touch rejection reclaim -> MSS -> broken-level retest"
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
                "source_pool_reuse": False,
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
        raise RuntimeError(f"auction-state variant failed: {variant} exit={process.exitcode}")
    path = args.output.resolve() / variant / "metrics.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid auction-state metrics: {path}")
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
        variant="baseline_auction_state_router",
    )
    ablation = _isolated(
        args=args,
        config_path=config_path,
        variant="ablation_rejection_only",
    )
    variants = {
        "baseline_auction_state_router": local._compact(baseline),
        "ablation_rejection_only": local._compact(ablation),
    }
    baseline_passed = bool(
        (variants["baseline_auction_state_router"].get("weekly_gate") or {}).get(
            "passed"
        )
    )
    summary = {
        "candidate": "candidate-07",
        "family": "local_15s_auction_state_router",
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
            "add mutually exclusive accepted-break continuation branch to the "
            "unchanged rejection-reversal branch"
        ),
        "variants": variants,
        "selected_variant": (
            "baseline_auction_state_router" if baseline_passed else None
        ),
        "eligible_for_frozen_week_2": baseline_passed,
        "interpretation": (
            "WEEK_1_GATE_PASSED"
            if baseline_passed
            else "AUCTION_STATE_ROUTER_FAILED_WEEK_1"
        ),
    }
    write_json_atomic(output / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main() -> int:
    return run(local.build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
