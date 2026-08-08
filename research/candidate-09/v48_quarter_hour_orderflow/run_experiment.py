#!/usr/bin/env python3
"""Evaluate quarter-hour first-10s order flow against a +7 minute placebo."""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


DEVELOPMENT = {
    "build_start": "2024-10-25",
    "build_end": "2024-11-30",
    "evaluation_start": "2024-11-01",
    "evaluation_end": "2024-11-30",
}
HOLDOUT = {
    "build_start": "2025-10-25",
    "build_end": "2025-11-30",
    "evaluation_start": "2025-11-01",
    "evaluation_end": "2025-11-30",
}
LONG = {
    "build_start": "2024-11-25",
    "build_end": "2025-10-31",
    "evaluation_start": "2024-12-01",
    "evaluation_end": "2025-10-31",
}
VARIANTS = {
    "quarter-hour": 0,
    "plus-seven-placebo": 7,
}


def period_days(period: dict[str, str]) -> int:
    return (
        date.fromisoformat(period["evaluation_end"])
        - date.fromisoformat(period["evaluation_start"])
    ).days + 1


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def configured(
    base: dict[str, Any],
    *,
    clock_offset: int,
    calendar_days: int,
) -> dict[str, Any]:
    cfg = json.loads(json.dumps(base))
    minimum_trades = max(14, math.ceil(0.5 * calendar_days))
    cfg["execution_seed"] = 480048
    cfg["gate"] = {
        "min_geometric_daily_growth": 0.01,
        "min_trades": minimum_trades,
        "min_wins": math.ceil(0.40 * minimum_trades),
        "min_win_rate": 0.40,
        "min_active_days": max(8, math.ceil(0.25 * calendar_days)),
        "max_drawdown": 0.30,
        "max_largest_winner_share": 0.35,
    }
    cfg["strategy"].update(
        {
            "candidate33_require_stacked_imbalance": False,
            "candidate33_min_stacked_levels": 3,
            "candidate33_stack_boundary_tolerance_atr": 0.25,
            "candidate33_trade_failed_auction": False,
            "candidate35_include_confirmed_swings": False,
            "candidate35_enable_15m": True,
            "candidate35_enable_60m": True,
            "candidate35_enable_daily": True,
            "candidate48_clock_offset_minutes": int(clock_offset),
            "candidate48_retest_timeout_bars": 4,
            "max_hold_bars": 240,
        }
    )
    return cfg


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    diagnostics = metrics.get("strategy_diagnostics", {})
    return {
        "gate_pass": bool(metrics.get("gate_pass", False)),
        "geometric_daily_growth": float(metrics.get("geometric_daily_growth", -1.0)),
        "total_return": float(metrics.get("total_return", -1.0)),
        "ending_nav": float(metrics.get("ending_nav", 0.0)),
        "trades": int(metrics.get("trades", 0)),
        "wins": int(metrics.get("wins", 0)),
        "losses": int(metrics.get("losses", 0)),
        "win_rate": float(metrics.get("win_rate", 0.0)),
        "profit_factor": metrics.get("profit_factor"),
        "expectancy_usdt": metrics.get("expectancy_usdt"),
        "max_drawdown": float(metrics.get("max_drawdown", 1.0)),
        "active_days": int(metrics.get("active_days", 0)),
        "largest_winner_share": float(metrics.get("largest_winner_share", 1.0)),
        "liquidations": int(metrics.get("liquidations", 0)),
        "gate_checks": metrics.get("gate_checks", {}),
        "scenario_metrics": metrics.get("scenario_metrics", {}),
        "diagnostics": {
            key: value
            for key, value in diagnostics.items()
            if key.startswith("candidate48_")
            or key.startswith("candidate35_")
            or key
            in {
                "candidate16_natural_objective_rejections",
                "entry_submissions",
                "order_rejections",
                "max_simultaneous_entry_intents",
                "max_open_positions_observed",
            }
        },
    }


def implementation_ok(metrics: dict[str, Any]) -> bool:
    checks = metrics.get("gate_checks", {})
    return all(
        bool(checks.get(name, False))
        for name in (
            "positive_nav",
            "no_liquidation",
            "no_order_rejections",
            "single_entry_intent",
            "single_position",
        )
    )


def run_stage(
    *,
    source: Path,
    cache: Path,
    output: Path,
    variant: str,
    period: dict[str, str],
    config: dict[str, Any],
) -> dict[str, Any]:
    destination = output / variant
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    config_path = destination / "config.json"
    write_json(config_path, config)
    command = [
        sys.executable,
        str(source / "research" / "candidate-16" / "candidate.py"),
        "stage",
        "--config",
        str(config_path.resolve()),
        "--build-start",
        period["build_start"],
        "--build-end",
        period["build_end"],
        "--evaluation-start",
        period["evaluation_start"],
        "--evaluation-end",
        period["evaluation_end"],
        "--cache",
        str(cache.resolve()),
        "--output",
        str(destination.resolve()),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str((source / "research" / "candidate-16").resolve()),
            str((source / "research" / "candidate-05").resolve()),
            str((Path.cwd() / "src").resolve()),
            env.get("PYTHONPATH", ""),
        ]
    )
    completed = subprocess.run(
        command,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    (destination / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (destination / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"{variant} failed with code {completed.returncode}: {completed.stderr[-5000:]}"
        )
    metrics_path = destination / "metrics.json"
    if not metrics_path.exists():
        raise RuntimeError(f"missing metrics: {metrics_path}")
    return json.loads(metrics_path.read_text(encoding="utf-8"))


def select(results: dict[str, dict[str, Any]]) -> str | None:
    eligible = [
        name
        for name, metrics in results.items()
        if implementation_ok(metrics) and bool(metrics.get("gate_pass"))
    ]
    return max(
        eligible,
        key=lambda name: (
            float(results[name].get("geometric_daily_growth", -1.0)),
            -float(results[name].get("max_drawdown", 1.0)),
        ),
        default=None,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads(
        (source / "research" / "candidate-16" / "config.json").read_text(
            encoding="utf-8"
        )
    )
    decision: dict[str, Any] = {
        "candidate": "candidate-09-v48-quarter-hour-orderflow",
        "source_lineage": {
            "external_mechanism": "quarter-hour periodic algorithmic order flow predicts later crypto futures returns",
            "context": "exact buyer/seller initiated notional from seconds 0-10 of the completed clock minute",
            "state": "same minute efficient directional price response and directional footprint POC",
            "execution": "first later retest of the signal-minute POC",
            "invalidation": "opposite signal-minute extreme",
            "target": "next unconsumed completed-auction objective after costs",
            "exact_placebo": "identical logic at minute offsets 07, 22, 37 and 52",
        },
        "development_period": DEVELOPMENT,
        "holdout_period_reserved": HOLDOUT,
        "long_period_reserved": LONG,
        "holdout_opened_once": False,
        "long_opened_once": False,
    }
    try:
        development_full = {
            name: run_stage(
                source=source,
                cache=args.cache / "development" / name,
                output=output / "development",
                variant=name,
                period=DEVELOPMENT,
                config=configured(
                    base,
                    clock_offset=offset,
                    calendar_days=period_days(DEVELOPMENT),
                ),
            )
            for name, offset in VARIANTS.items()
        }
    except Exception as exc:
        decision["status"] = "IMPLEMENTATION_ERROR"
        decision["error"] = f"{type(exc).__name__}: {exc}"
        write_json(output / "FINAL_DECISION.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 2

    decision["development"] = {
        name: compact(metrics) for name, metrics in development_full.items()
    }
    selected = select(development_full)
    decision["selected_variant"] = selected
    if selected is None:
        decision["status"] = (
            "IMPLEMENTATION_ERROR"
            if not all(implementation_ok(value) for value in development_full.values())
            else "LOGIC_ERROR_NO_STRUCTURAL_PATH"
        )
        write_json(output / "FINAL_DECISION.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 2 if decision["status"] == "IMPLEMENTATION_ERROR" else 0

    decision["holdout_opened_once"] = True
    try:
        holdout = run_stage(
            source=source,
            cache=args.cache / "holdout" / selected,
            output=output / "holdout",
            variant=selected,
            period=HOLDOUT,
            config=configured(
                base,
                clock_offset=VARIANTS[selected],
                calendar_days=period_days(HOLDOUT),
            ),
        )
    except Exception as exc:
        decision["status"] = "HOLDOUT_IMPLEMENTATION_ERROR"
        decision["holdout_error"] = f"{type(exc).__name__}: {exc}"
        write_json(output / "FINAL_DECISION.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 2

    decision["holdout"] = compact(holdout)
    if not implementation_ok(holdout):
        decision["status"] = "HOLDOUT_IMPLEMENTATION_ERROR"
    elif not bool(holdout.get("gate_pass")):
        decision["status"] = "HOLDOUT_LOGIC_FAIL_FAMILY_RETIRED"
    else:
        decision["long_opened_once"] = True
        try:
            long_result = run_stage(
                source=source,
                cache=args.cache / "long" / selected,
                output=output / "long",
                variant=selected,
                period=LONG,
                config=configured(
                    base,
                    clock_offset=VARIANTS[selected],
                    calendar_days=period_days(LONG),
                ),
            )
            decision["long"] = compact(long_result)
            if not implementation_ok(long_result):
                decision["status"] = "LONG_IMPLEMENTATION_ERROR"
            elif bool(long_result.get("gate_pass")):
                decision["status"] = "TARGET_VALIDATED_LONG_CONTINUOUS"
            else:
                decision["status"] = "LONG_LOGIC_FAIL_FAMILY_RETIRED"
        except Exception as exc:
            decision["status"] = "LONG_IMPLEMENTATION_ERROR"
            decision["long_error"] = f"{type(exc).__name__}: {exc}"

    write_json(output / "FINAL_DECISION.json", decision)
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0 if "IMPLEMENTATION_ERROR" not in decision["status"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
