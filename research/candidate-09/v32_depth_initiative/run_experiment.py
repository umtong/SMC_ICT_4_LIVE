#!/usr/bin/env python3
"""Run Candidate 09 v32 development and one-shot holdout via NautilusTrader."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


DEVELOPMENT = {
    "build_start": "2024-12-30",
    "build_end": "2025-02-02",
    "evaluation_start": "2025-01-06",
    "evaluation_end": "2025-02-02",
}
HOLDOUT = {
    "build_start": "2025-01-27",
    "build_end": "2025-03-02",
    "evaluation_start": "2025-02-03",
    "evaluation_end": "2025-03-02",
}
VARIANTS = {
    "baseline": True,
    "no-depth": False,
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def configured(base: dict[str, Any], use_depth: bool) -> dict[str, Any]:
    cfg = json.loads(json.dumps(base))
    cfg["execution_seed"] = 320032
    cfg["gate"] = {
        "min_geometric_daily_growth": 0.01,
        "min_trades": 14,
        "min_wins": 7,
        "min_win_rate": 0.40,
        "min_active_days": 8,
        "max_drawdown": 0.30,
        "max_largest_winner_share": 0.35,
    }
    cfg["strategy"].update(
        {
            "candidate32_use_displayed_depth": bool(use_depth),
            "candidate32_trade_acceptance": False,
            "candidate32_min_defending_depth_refill": 0.01,
            "candidate32_initiative_timeout_bars": 3,
            "candidate32_min_initiative_body_atr": 0.20,
            "candidate32_min_initiative_flow": 0.08,
            "candidate32_min_initiative_efficiency": 0.25,
            "candidate32_min_initiative_close_location": 0.60,
            "candidate32_min_initiative_depth_support": 0.01,
        }
    )
    return cfg


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    diagnostics = metrics.get("strategy_diagnostics", {})
    return {
        "gate_pass": bool(metrics.get("gate_pass", False)),
        "geometric_daily_growth": float(
            metrics.get("geometric_daily_growth", -1.0)
        ),
        "total_return": float(metrics.get("total_return", -1.0)),
        "ending_nav": float(metrics.get("ending_nav", 0.0)),
        "trades": int(metrics.get("trades", 0)),
        "wins": int(metrics.get("wins", 0)),
        "losses": int(metrics.get("losses", 0)),
        "win_rate": float(metrics.get("win_rate", 0.0)),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown": float(metrics.get("max_drawdown", 1.0)),
        "active_days": int(metrics.get("active_days", 0)),
        "largest_winner_share": float(
            metrics.get("largest_winner_share", 1.0)
        ),
        "liquidations": int(metrics.get("liquidations", 0)),
        "gate_checks": metrics.get("gate_checks", {}),
        "scenario_metrics": metrics.get("scenario_metrics", {}),
        "diagnostics": {
            key: value
            for key, value in diagnostics.items()
            if key.startswith("candidate32_")
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
    stage_dir = output / variant
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    config_path = stage_dir / "config.json"
    write_json(config_path, config)
    candidate = source / "research" / "candidate-16" / "candidate.py"
    env = os.environ.copy()
    python_paths = [
        str((source / "research" / "candidate-16").resolve()),
        str((source / "research" / "candidate-05").resolve()),
        str((Path.cwd() / "src").resolve()),
    ]
    env["PYTHONPATH"] = os.pathsep.join(
        python_paths + [env.get("PYTHONPATH", "")]
    )
    command = [
        sys.executable,
        str(candidate),
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
        str(stage_dir.resolve()),
    ]
    completed = subprocess.run(
        command,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    (stage_dir / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (stage_dir / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"{variant} failed with code {completed.returncode}: "
            f"{completed.stderr[-4000:]}"
        )
    metrics_path = stage_dir / "metrics.json"
    if not metrics_path.exists():
        raise RuntimeError(f"missing metrics: {metrics_path}")
    return json.loads(metrics_path.read_text(encoding="utf-8"))


def select_variant(results: dict[str, dict[str, Any]]) -> str | None:
    passed = [
        name for name, metrics in results.items() if metrics.get("gate_pass")
    ]
    if not passed:
        return None
    return max(
        passed,
        key=lambda name: (
            float(results[name].get("geometric_daily_growth", -1.0)),
            -float(results[name].get("max_drawdown", 1.0)),
            int(results[name].get("trades", 0)),
        ),
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
    base_config = json.loads(
        (source / "research" / "candidate-16" / "config.json").read_text(
            encoding="utf-8"
        )
    )

    development_full: dict[str, dict[str, Any]] = {}
    try:
        for variant, use_depth in VARIANTS.items():
            development_full[variant] = run_stage(
                source=source,
                cache=args.cache / "development",
                output=output / "development",
                variant=variant,
                period=DEVELOPMENT,
                config=configured(base_config, use_depth),
            )
    except Exception as exc:
        decision = {
            "candidate": "candidate-09-v32-depth-defense-later-initiative",
            "status": "IMPLEMENTATION_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "development_period": DEVELOPMENT,
            "holdout_opened_once": False,
        }
        write_json(output / "FINAL_DECISION.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 2

    selected = select_variant(development_full)
    development = {
        name: compact(value) for name, value in development_full.items()
    }
    all_implementation_ok = all(
        implementation_ok(value) for value in development_full.values()
    )
    if not all_implementation_ok:
        status = "IMPLEMENTATION_ERROR"
    elif selected is None:
        status = "LOGIC_ERROR_NO_STRUCTURAL_PATH"
    else:
        status = "DEVELOPMENT_GATE_PASS"

    decision: dict[str, Any] = {
        "candidate": "candidate-09-v32-depth-defense-later-initiative",
        "source_lineage": {
            "candidate16_v1_commit": (
                "91317f522546afd837e330a2bde0f9c05e81b068"
            ),
            "reused": (
                "Candidate 05 data/NautilusTrader stack and Candidate 16 v1 "
                "parent-auction detector"
            ),
            "changed": (
                "failed-auction depth defense plus later independent initiative "
                "and fail-close protection"
            ),
        },
        "status": status,
        "development_period": DEVELOPMENT,
        "development": development,
        "selected_variant": selected,
        "holdout_opened_once": False,
        "known_exact_control": {
            "name": "candidate-16-v1-immediate-failed-auction-entry",
            "result": "REJECTED",
            "trades": 167,
            "wins": 38,
            "losses": 129,
            "total_return": -0.97478631,
        },
    }

    if selected is not None and all_implementation_ok:
        selected_config = configured(base_config, VARIANTS[selected])
        try:
            holdout_full = run_stage(
                source=source,
                cache=args.cache / "holdout",
                output=output / "holdout",
                variant=selected,
                period=HOLDOUT,
                config=selected_config,
            )
            decision["holdout_opened_once"] = True
            decision["holdout_period"] = HOLDOUT
            decision["holdout"] = compact(holdout_full)
            if not implementation_ok(holdout_full):
                decision["status"] = "HOLDOUT_IMPLEMENTATION_ERROR"
            elif holdout_full.get("gate_pass"):
                decision["status"] = (
                    "HOLDOUT_GATE_PASS_REQUIRES_LONG_CONTINUOUS_EVALUATION"
                )
            else:
                decision["status"] = "HOLDOUT_LOGIC_FAIL_FAMILY_RETIRED"
        except Exception as exc:
            decision["holdout_opened_once"] = True
            decision["status"] = "HOLDOUT_IMPLEMENTATION_ERROR"
            decision["holdout_error"] = f"{type(exc).__name__}: {exc}"

    write_json(output / "FINAL_DECISION.json", decision)
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0 if decision["status"] != "IMPLEMENTATION_ERROR" else 2


if __name__ == "__main__":
    raise SystemExit(main())
