#!/usr/bin/env python3
"""Compare 5S and 15S execution clocks after the same 15S liquidity sweep."""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from pathlib import Path
from typing import Any

import backtest as base
import run_local_liquidity_sweep_mss_retest as local
from multiclock_sweep_mss_scenario import (
    build_five_second_signals,
    discover_five_second,
)
from nested_liquidity_sweep_scenario import (
    build_causal_signals as build_fifteen_second_signals,
    discover as discover_fifteen_second,
)
from smc_ict_4.manifest import write_json_atomic


def _worker(
    args: argparse.Namespace,
    config_path: Path,
    variant: str,
    execution_timeframe: str,
) -> None:
    original_discover = local.discover_structural_signals
    original_builder = local.build_causal_signals
    if execution_timeframe == "5S":
        local.discover_structural_signals = discover_five_second
        local.build_causal_signals = build_five_second_signals
    else:
        local.discover_structural_signals = (
            lambda *, config, bundle, start, end, require_retest: discover_fifteen_second(
                config=config,
                bundle=bundle,
                start=start,
                end=end,
                require_retest=require_retest,
                include_higher_sources=False,
            )
        )
        local.build_causal_signals = build_fifteen_second_signals
    try:
        metrics = local._run_variant(
            args=args,
            config_path=config_path,
            variant=variant,
            require_retest=True,
        )
        metrics["execution_contract"].update(
            {
                "selected_route": (
                    "15S source sweep -> distinct "
                    + execution_timeframe
                    + " MSS -> broken-level retest"
                ),
                "source_timeframe": "15S",
                "execution_timeframe": execution_timeframe,
                "wall_clock_windows_unchanged": True,
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
    execution_timeframe: str,
) -> dict[str, Any]:
    process = mp.get_context("spawn").Process(
        target=_worker,
        args=(args, config_path, variant, execution_timeframe),
    )
    process.start()
    process.join()
    if process.exitcode != 0:
        raise RuntimeError(f"execution-clock variant failed: {variant} exit={process.exitcode}")
    path = args.output.resolve() / variant / "metrics.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid execution-clock metrics: {path}")
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
        variant="baseline_5s_execution",
        execution_timeframe="5S",
    )
    ablation = _isolated(
        args=args,
        config_path=config_path,
        variant="ablation_15s_execution",
        execution_timeframe="15S",
    )
    variants = {
        "baseline_5s_execution": local._compact(baseline),
        "ablation_15s_execution": local._compact(ablation),
    }
    passed = [
        name
        for name, value in variants.items()
        if bool((value.get("weekly_gate") or {}).get("passed"))
    ]
    selected = (
        max(
            passed,
            key=lambda name: (
                float(variants[name]["daily_geometric_growth"]),
                float(variants[name]["profit_factor"] or 0.0),
                int(variants[name]["trades"]),
            ),
        )
        if passed
        else None
    )
    summary = {
        "candidate": "candidate-07",
        "family": "15S_sweep_multiclock_MSS_retest",
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
        "controlled_variable": "execution structure clock: 5S versus 15S",
        "variants": variants,
        "selected_variant": selected,
        "eligible_for_frozen_week_2": selected is not None,
        "interpretation": (
            "WEEK_1_GATE_PASSED"
            if selected
            else "FIVE_SECOND_AND_FIFTEEN_SECOND_EXECUTION_FAILED"
        ),
    }
    write_json_atomic(output / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main() -> int:
    return run(local.build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
