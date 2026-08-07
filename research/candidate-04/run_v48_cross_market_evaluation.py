#!/usr/bin/env python3
"""Compile and execute one cross-market four-instrument week.

This is orchestration only. A selected pattern/scenario compiler emits
completed-data intents; the causal target registry preserves or declares only
pre-existing external destinations; the trusted multi-asset BacktestNode owns
orders, fills, costs, positions, risk, PnL and NAV.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


C04 = Path(__file__).resolve().parent
ROOT = C04.parent.parent
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
FOLLOWERS = SYMBOLS[1:]
DEFAULT_COMPILER = "cross_market_information_transfer_compiler_v2.py"
DEFAULT_CANDIDATE = "candidate-04-v48-cross-market-information-transfer"
DEFAULT_MARKET_CAUSE = (
    "BTC information event, follower underreaction, independent follower "
    "inventory and structure confirmation"
)


@dataclass(frozen=True, slots=True)
class StageError(RuntimeError):
    stage: str
    code: int

    def __str__(self) -> str:
        return f"{self.stage} failed with exit code {self.code}"


def run(command: list[str], *, env: dict[str, str], log: Path, stage: str) -> None:
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
        raise StageError(stage, result.returncode)


def read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def discover_rich(restored: Path, symbol: str) -> Path:
    matches = sorted(
        path.parent
        for path in restored.rglob("data_manifest.json")
        if symbol in str(path)
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one restored rich directory for {symbol}, found {len(matches)}"
        )
    return matches[0]


def prepare_inputs(restored: Path, output: Path) -> None:
    rich_root = output / "rich"
    config_root = output / "config"
    rich_root.mkdir(parents=True, exist_ok=True)
    config_root.mkdir(parents=True, exist_ok=True)
    base_config = json.loads(
        (C04 / "inventory_transfer_config.json").read_text(encoding="utf-8")
    )
    for symbol in SYMBOLS:
        source = discover_rich(restored, symbol).resolve()
        link = rich_root / symbol
        if link.is_symlink() or link.exists():
            if link.is_symlink():
                link.unlink()
            else:
                raise RuntimeError(f"rich destination already exists: {link}")
        link.symlink_to(source, target_is_directory=True)
        config = dict(base_config)
        config["symbol"] = symbol
        (config_root / f"{symbol}.json").write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def copy_compiler_stream(all_signals: Path, signals: Path, symbol: str) -> None:
    destination = signals / symbol
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("signals.json", "summary.json"):
        (destination / name).write_bytes((all_signals / symbol / name).read_bytes())


def candidate_decision(
    *,
    implementation: bool,
    potential: bool,
    trades: int,
) -> str:
    if not implementation:
        return "implementation_failure_repair_and_rerun_identical_week"
    if potential:
        return "retain_candidate_and_open_second_predeclared_four_asset_week"
    if trades == 0:
        return "diagnose_state_bottleneck_then_one_variable_ablation"
    return "run_one_core_variable_ablation_on_identical_week_before_discard"


def evidence_payload(
    args: argparse.Namespace,
    output: Path,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    compiler = read_json(output / "all_signals/summary.json")
    metrics = read_json(output / "nautilus/metrics.json")
    risk = read_json(output / "nautilus/risk_evidence.json")
    targets = {
        symbol: value
        for symbol in FOLLOWERS
        if (value := read_json(output / "signals" / symbol / "summary.json"))
        is not None
    }
    implementation = bool(
        failure is None
        and compiler is not None
        and metrics is not None
        and risk is not None
        and metrics.get("global_entry_pass")
        and metrics.get("risk_pass")
        and risk.get("risk_pass")
    )
    trades = int((metrics or {}).get("trades") or 0)
    active_days = int((metrics or {}).get("active_days") or 0)
    total_return = float((metrics or {}).get("total_return") or 0.0)
    win_rate = float((metrics or {}).get("win_rate") or 0.0)
    potential = bool(
        implementation
        and total_return > 0.0
        and trades >= 3
        and active_days >= 2
        and win_rate >= 0.50
    )
    decision = candidate_decision(
        implementation=implementation,
        potential=potential,
        trades=trades,
    )
    return {
        "candidate": args.candidate,
        "compiler_script": args.compiler_script,
        "targets_predeclared": bool(args.targets_predeclared),
        "engine": "NautilusTrader 1.230.0 BacktestNode",
        "source_feature_workflow_run": args.source_feature_run,
        "evaluation": {
            "build_start": args.build_start,
            "build_end": args.build_end,
            "evaluation_start": args.evaluation_start,
            "evaluation_end": args.evaluation_end,
        },
        "market_cause": args.market_cause,
        "compiler": compiler,
        "target_summaries": targets,
        "nautilus_metrics": metrics,
        "risk_evidence": risk,
        "runtime_failure": failure,
        "implementation_pass": implementation,
        "positive_first_week_potential": potential,
        "decision": decision,
        "project_target_reached": False,
        "final_validation_completed": False,
        "performance_recalculated_outside_nautilus": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler-script", default=DEFAULT_COMPILER)
    parser.add_argument("--candidate", default=DEFAULT_CANDIDATE)
    parser.add_argument("--market-cause", default=DEFAULT_MARKET_CAUSE)
    parser.add_argument("--targets-predeclared", action="store_true")
    parser.add_argument("--restored-rich", type=Path, required=True)
    parser.add_argument("--source-feature-run", type=int, required=True)
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()

    compiler_path = (C04 / args.compiler_script).resolve()
    if compiler_path.parent != C04 or not compiler_path.is_file():
        raise SystemExit(f"invalid compiler script: {args.compiler_script}")

    restored = args.restored_rich.resolve()
    output = args.output.resolve()
    cache = args.cache.resolve()
    evidence = args.evidence.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    evidence.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(C04),
        C04_BUILD_START=args.build_start,
        C04_BUILD_END=args.build_end,
        C04_EVALUATION_START=args.evaluation_start,
        C04_EVALUATION_END=args.evaluation_end,
    )

    failure: dict[str, Any] | None = None
    try:
        prepare_inputs(restored, output)
        all_signals = output / "all_signals"
        signals = output / "signals"
        run(
            [
                sys.executable,
                str(compiler_path),
                "--rich-root",
                str(output / "rich"),
                "--config-root",
                str(output / "config"),
                "--kline-root",
                str(cache / "compiler-klines"),
                "--evaluation-start",
                args.evaluation_start,
                "--evaluation-end",
                args.evaluation_end,
                "--output",
                str(all_signals),
            ],
            env=env,
            log=output / "compiler.log",
            stage=f"compiler_{compiler_path.stem}",
        )
        copy_compiler_stream(all_signals, signals, "BTCUSDT")
        if args.targets_predeclared:
            for symbol in FOLLOWERS:
                copy_compiler_stream(all_signals, signals, symbol)
        else:
            for symbol in FOLLOWERS:
                run(
                    [
                        sys.executable,
                        str(C04 / "causal_target_registry_enricher_for_symbol.py"),
                        "--signals",
                        str(all_signals / symbol / "signals.json"),
                        "--base-config",
                        str(output / "config" / f"{symbol}.json"),
                        "--rich-dir",
                        str(output / "rich" / symbol),
                        "--kline-dir",
                        str(cache / "compiler-klines" / symbol),
                        "--build-start",
                        args.build_start,
                        "--build-end",
                        args.build_end,
                        "--output-dir",
                        str(signals / symbol),
                        "--download-klines",
                        "--cost-rate",
                        "0.00075",
                        "--minimum-net-r",
                        "1.20",
                    ],
                    env=env,
                    log=output / f"target-{symbol}.log",
                    stage=f"causal_target_{symbol}",
                )
        run(
            [
                sys.executable,
                str(C04 / "nt_multi_asset_rich_backtest_v44.py"),
                "--config",
                str(C04 / "nt_liquidity_config.json"),
                "--signals-root",
                str(signals),
                "--build-start",
                args.build_start,
                "--build-end",
                args.build_end,
                "--evaluation-start",
                args.evaluation_start,
                "--evaluation-end",
                args.evaluation_end,
                "--cache",
                str(cache / "nautilus"),
                "--output",
                str(output / "nautilus"),
                "--min-trades",
                "3",
                "--min-active-days",
                "2",
                "--min-win-rate",
                "0.50",
                "--min-geometric-daily",
                "0.0",
            ],
            env=env,
            log=output / "nautilus.log",
            stage="one_account_nautilus",
        )
    except Exception as exc:
        failure = {
            "stage": getattr(exc, "stage", type(exc).__name__),
            "return_code": getattr(exc, "code", None),
            "error": str(exc),
        }

    payload = evidence_payload(args, output, failure)
    evidence.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "decision.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if failure is not None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
