#!/usr/bin/env python3
"""Compile the frozen V44 causal signal stream for one allowed instrument.

This is orchestration only: it builds completed-data rich observations, runs the
frozen V31 state compiler, removes the already rejected stress-continuation
scenario, and enriches only missing destinations with pre-existing liquidity.
It never matches orders, sizes positions, computes PnL or updates NAV.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


C04 = Path(__file__).resolve().parent
ROOT = C04.parent.parent
SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}
REMOVED_SCENARIO = "STRESS_SETTLED_ACCEPTANCE_CONTINUATION"


class CompileStageError(RuntimeError):
    pass


def run(command: list[str], env: dict[str, str], log: Path, stage: str) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if result.returncode:
        raise CompileStageError(f"{stage} failed with exit code {result.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, choices=sorted(SYMBOLS))
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output, cache = args.output.resolve(), args.cache.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(C04),
        C04_BUILD_START=args.build_start,
        C04_BUILD_END=args.build_end,
        C04_EVALUATION_START=args.evaluation_start,
        C04_EVALUATION_END=args.evaluation_end,
    )

    config = json.loads((C04 / "inventory_transfer_config.json").read_text())
    config["symbol"] = args.symbol
    config_path = output / "config.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")

    rich = output / "rich"
    all_signals = output / "v31_all"
    core = output / "v31_core"
    signals = output / "signals"

    run(
        [
            sys.executable,
            str(C04 / "rich_features_v2.py"),
            "--symbol",
            args.symbol,
            "--start",
            args.build_start,
            "--end",
            args.build_end,
            "--cache",
            str(cache / "rich"),
            "--output",
            str(rich),
        ],
        env,
        output / "rich.log",
        "rich_features",
    )
    run(
        [
            sys.executable,
            str(C04 / "boundary_negotiation_expansion_compiler.py"),
            "--base-config",
            str(config_path),
            "--impact-config",
            str(C04 / "impact_exhaustion_config.json"),
            "--router-config",
            str(C04 / "auction_activity_router_config.json"),
            "--rich-dir",
            str(rich),
            "--kline-dir",
            str(cache / "compiler-klines"),
            "--evaluation-start",
            args.evaluation_start,
            "--evaluation-end",
            args.evaluation_end,
            "--output",
            str(all_signals),
            "--download-klines",
        ],
        env,
        output / "compiler.log",
        "boundary_negotiation_compiler",
    )
    run(
        [
            sys.executable,
            str(C04 / "ablate_compiled_scenario.py"),
            "--input-signals",
            str(all_signals / "signals.json"),
            "--input-summary",
            str(all_signals / "summary.json"),
            "--remove",
            REMOVED_SCENARIO,
            "--candidate",
            "candidate-04-v46-frozen-v44-core",
            "--output",
            str(core),
        ],
        env,
        output / "core-ablation.log",
        "remove_rejected_stress_continuation",
    )
    run(
        [
            sys.executable,
            str(C04 / "causal_target_registry_enricher.py"),
            "--signals",
            str(core / "signals.json"),
            "--base-config",
            str(config_path),
            "--rich-dir",
            str(rich),
            "--kline-dir",
            str(cache / "enrichment-klines"),
            "--build-start",
            args.build_start,
            "--build-end",
            args.build_end,
            "--output-dir",
            str(signals),
            "--download-klines",
            "--cost-rate",
            "0.00075",
            "--minimum-net-r",
            "1.20",
        ],
        env,
        output / "enrichment.log",
        "causal_target_enrichment",
    )

    summary = {
        "candidate": "candidate-04-v46-four-asset-v44",
        "symbol": args.symbol,
        "build_start": args.build_start,
        "build_end": args.build_end,
        "evaluation_start": args.evaluation_start,
        "evaluation_end": args.evaluation_end,
        "removed_scenario": REMOVED_SCENARIO,
        "compiler_summary": json.loads((all_signals / "summary.json").read_text()),
        "core_summary": json.loads((core / "summary.json").read_text()),
        "target_summary": json.loads((signals / "summary.json").read_text()),
        "performance_calculated": false if False else False,
        "execution": "deferred to one-account NautilusTrader runner",
    }
    (output / "compile_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
