#!/usr/bin/env python3
"""Run local sweep/MSS variants in isolated NautilusTrader processes.

NautilusTrader owns a process-global Rust logger. This wrapper changes no market
logic, period, risk, cost, target, stop, fill or accounting rule. It runs the
frozen baseline and its single retest ablation in separate spawned processes,
then combines only their already-written authoritative metrics.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from pathlib import Path
from typing import Any

import run_local_liquidity_sweep_mss_retest as candidate
from smc_ict_4.manifest import write_json_atomic


def _worker(
    args: argparse.Namespace,
    config_path: Path,
    variant: str,
    require_retest: bool,
) -> None:
    candidate._run_variant(
        args=args,
        config_path=config_path,
        variant=variant,
        require_retest=require_retest,
    )


def _isolated(
    *,
    args: argparse.Namespace,
    config_path: Path,
    variant: str,
    require_retest: bool,
) -> dict[str, Any]:
    process = mp.get_context("spawn").Process(
        target=_worker,
        args=(args, config_path, variant, require_retest),
    )
    process.start()
    process.join()
    if process.exitcode != 0:
        raise RuntimeError(
            f"isolated Nautilus variant failed: {variant} exit={process.exitcode}"
        )
    metrics_path = args.output.resolve() / variant / "metrics.json"
    if not metrics_path.is_file():
        raise RuntimeError(f"variant metrics missing: {metrics_path}")
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"variant metrics are not an object: {metrics_path}")
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
        variant="baseline_break_retest",
        require_retest=True,
    )
    ablation = _isolated(
        args=args,
        config_path=config_path,
        variant="ablation_mss_close",
        require_retest=False,
    )
    variants = {
        "baseline_break_retest": candidate._compact(baseline),
        "ablation_mss_close": candidate._compact(ablation),
    }
    passed = [
        name
        for name, value in variants.items()
        if bool((value.get("weekly_gate") or {}).get("passed"))
    ]
    if passed:
        selected = max(
            passed,
            key=lambda name: (
                float(variants[name]["daily_geometric_growth"]),
                float(variants[name]["profit_factor"] or 0.0),
                int(variants[name]["trades"]),
            ),
        )
        interpretation = "WEEK_1_GATE_PASSED"
    else:
        selected = None
        interpretation = "BASELINE_AND_SINGLE_ABLATION_FAILED"
    summary = {
        "candidate": "candidate-07",
        "family": "local_15s_liquidity_sweep_mss_retest",
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
        "variants": variants,
        "selected_variant": selected,
        "eligible_for_frozen_week_2": selected is not None,
        "interpretation": interpretation,
    }
    write_json_atomic(output / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main() -> int:
    return run(candidate.build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
