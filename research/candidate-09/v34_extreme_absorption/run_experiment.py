#!/usr/bin/env python3
"""Chronological v34 development, one-shot holdout, conditional long run."""
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
    "build_start": "2024-07-25",
    "build_end": "2024-08-31",
    "evaluation_start": "2024-08-01",
    "evaluation_end": "2024-08-31",
}
HOLDOUT = {
    "build_start": "2025-07-25",
    "build_end": "2025-08-31",
    "evaluation_start": "2025-08-01",
    "evaluation_end": "2025-08-31",
}
LONG = {
    "build_start": "2024-08-25",
    "build_end": "2025-07-31",
    "evaluation_start": "2024-09-01",
    "evaluation_end": "2025-07-31",
}
VARIANTS = {"baseline": True, "no-extreme-absorption": False}


def days(period: dict[str, str]) -> int:
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
    require_absorption: bool,
    calendar_days: int,
) -> dict[str, Any]:
    cfg = json.loads(json.dumps(base))
    minimum_trades = max(14, math.ceil(0.5 * calendar_days))
    cfg["execution_seed"] = 340034
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
            "candidate34_require_extreme_absorption": bool(require_absorption),
            "candidate34_min_extreme_delta": 0.55,
            "candidate34_min_extreme_notional_share": 0.04,
            "candidate34_min_extreme_cell_multiple": 2.0,
            "candidate34_initiative_timeout_bars": 3,
            "candidate34_min_opposite_stack_levels": 3,
            "candidate34_initiative_body_atr": 0.20,
            "candidate34_initiative_flow": 0.08,
            "candidate34_initiative_efficiency": 0.25,
            "candidate34_initiative_close_location": 0.60,
            "candidate34_stack_structure_tolerance_atr": 0.50,
            "candidate34_pullback_timeout_bars": 3,
            "candidate34_pullback_min_fraction": 0.35,
            "candidate34_pullback_max_fraction": 0.65,
            "candidate34_pullback_hold_fraction": 0.50,
            "candidate34_pullback_max_counterflow": 0.08,
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
        "max_drawdown": float(metrics.get("max_drawdown", 1.0)),
        "active_days": int(metrics.get("active_days", 0)),
        "largest_winner_share": float(metrics.get("largest_winner_share", 1.0)),
        "liquidations": int(metrics.get("liquidations", 0)),
        "gate_checks": metrics.get("gate_checks", {}),
        "scenario_metrics": metrics.get("scenario_metrics", {}),
        "diagnostics": {
            key: value
            for key, value in diagnostics.items()
            if key.startswith("candidate34_")
            or key
            in {
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
        if implementation_ok(metrics) and metrics.get("gate_pass")
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
        "candidate": "candidate-09-v34-extreme-absorption-later-initiative",
        "source_lineage": {
            "candidate16_v1_commit": "91317f522546afd837e330a2bde0f9c05e81b068",
            "reused": "Candidate 05 verified archives/Nautilus and Candidate 16 parent auction",
            "new_state": "aggressive notional concentrated at five extreme ticks without price progress",
            "new_transition": "later opposite stacked imbalance and price-flow initiative",
            "execution": "later 35-65% initiative-bar pullback; parent excursion stop; natural target",
        },
        "development_period": DEVELOPMENT,
        "holdout_period_reserved": HOLDOUT,
        "long_period_reserved": LONG,
        "holdout_opened_once": False,
        "long_opened_once": False,
        "known_controls": {
            "candidate16_v1_immediate_reversal": "167 trades; 38 wins; -97.478631%",
            "candidate09_v32_coarse_depth_later_initiative": "30 trades; 6 wins; -35.4976%",
        },
    }
    try:
        development_full = {
            name: run_stage(
                source=source,
                cache=args.cache / "development",
                output=output / "development",
                variant=name,
                period=DEVELOPMENT,
                config=configured(
                    base,
                    require_absorption=require_absorption,
                    calendar_days=days(DEVELOPMENT),
                ),
            )
            for name, require_absorption in VARIANTS.items()
        }
    except Exception as exc:
        decision.update(
            {
                "status": "IMPLEMENTATION_ERROR",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        write_json(output / "FINAL_DECISION.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 2

    decision["development"] = {
        name: compact(value) for name, value in development_full.items()
    }
    selected = select(development_full)
    decision["selected_variant"] = selected
    if selected is None:
        decision["status"] = "LOGIC_ERROR_NO_STRUCTURAL_PATH"
        write_json(output / "FINAL_DECISION.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 0

    decision["holdout_opened_once"] = True
    try:
        holdout = run_stage(
            source=source,
            cache=args.cache / "holdout",
            output=output / "holdout",
            variant=selected,
            period=HOLDOUT,
            config=configured(
                base,
                require_absorption=VARIANTS[selected],
                calendar_days=days(HOLDOUT),
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
    elif not holdout.get("gate_pass"):
        decision["status"] = "HOLDOUT_LOGIC_FAIL_FAMILY_RETIRED"
    else:
        decision["long_opened_once"] = True
        try:
            long_result = run_stage(
                source=source,
                cache=args.cache / "long",
                output=output / "long",
                variant=selected,
                period=LONG,
                config=configured(
                    base,
                    require_absorption=VARIANTS[selected],
                    calendar_days=days(LONG),
                ),
            )
            decision["long"] = compact(long_result)
            if not implementation_ok(long_result):
                decision["status"] = "LONG_IMPLEMENTATION_ERROR"
            elif long_result.get("gate_pass"):
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
