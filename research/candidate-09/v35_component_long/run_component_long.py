#!/usr/bin/env python3
"""Run the frozen V35 component on its pre-reserved continuous interval."""
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


PERIOD = {
    "build_start": "2024-07-25",
    "build_end": "2025-06-30",
    "evaluation_start": "2024-08-01",
    "evaluation_end": "2025-06-30",
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(
        (source / "research" / "candidate-16" / "config.json").read_text(
            encoding="utf-8"
        )
    )
    calendar_days = (
        date.fromisoformat(PERIOD["evaluation_end"])
        - date.fromisoformat(PERIOD["evaluation_start"])
    ).days + 1
    minimum_trades = math.ceil(0.5 * calendar_days)
    config["execution_seed"] = 350035
    config["gate"] = {
        "min_geometric_daily_growth": 0.01,
        "min_trades": minimum_trades,
        "min_wins": math.ceil(0.40 * minimum_trades),
        "min_win_rate": 0.40,
        "min_active_days": math.ceil(0.25 * calendar_days),
        "max_drawdown": 0.30,
        "max_largest_winner_share": 0.35,
    }
    config["strategy"].update(
        {
            "candidate33_require_stacked_imbalance": True,
            "candidate33_min_stacked_levels": 3,
            "candidate33_stack_boundary_tolerance_atr": 0.25,
            "candidate33_trade_failed_auction": False,
            "candidate35_include_confirmed_swings": False,
            "candidate35_enable_15m": True,
            "candidate35_enable_60m": True,
            "candidate35_enable_daily": True,
        }
    )
    config_path = output / "config.json"
    write_json(config_path, config)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str((source / "research" / "candidate-16").resolve()),
            str((source / "research" / "candidate-05").resolve()),
            str((Path.cwd() / "src").resolve()),
            env.get("PYTHONPATH", ""),
        ]
    )
    command = [
        sys.executable,
        str(source / "research" / "candidate-16" / "candidate.py"),
        "stage",
        "--config",
        str(config_path),
        "--build-start",
        PERIOD["build_start"],
        "--build-end",
        PERIOD["build_end"],
        "--evaluation-start",
        PERIOD["evaluation_start"],
        "--evaluation-end",
        PERIOD["evaluation_end"],
        "--cache",
        str(args.cache.resolve()),
        "--output",
        str(output),
    ]
    completed = subprocess.run(
        command,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    (output / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (output / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    decision: dict[str, Any] = {
        "candidate": "candidate-09-v35-completed-auction-footprint-component-long",
        "period": PERIOD,
        "source": "frozen V35 baseline; no confirmed swings; no parameter changes",
    }
    if completed.returncode != 0 or not (output / "metrics.json").exists():
        decision["status"] = "IMPLEMENTATION_ERROR"
        decision["returncode"] = completed.returncode
        decision["error_tail"] = completed.stderr[-5000:]
        write_json(output / "FINAL_DECISION.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 2
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    checks = metrics.get("gate_checks", {})
    implementation_ok = all(
        bool(checks.get(name, False))
        for name in (
            "positive_nav",
            "no_liquidation",
            "no_order_rejections",
            "single_entry_intent",
            "single_position",
        )
    )
    decision["metrics"] = {
        key: metrics.get(key)
        for key in (
            "starting_nav",
            "ending_nav",
            "total_return",
            "geometric_daily_growth",
            "trades",
            "wins",
            "losses",
            "win_rate",
            "profit_factor",
            "expectancy",
            "max_drawdown",
            "active_days",
            "largest_winner_share",
            "liquidations",
            "gate_pass",
            "gate_checks",
            "scenario_metrics",
            "strategy_diagnostics",
        )
    }
    if not implementation_ok:
        decision["status"] = "IMPLEMENTATION_ERROR"
    elif metrics.get("gate_pass"):
        decision["status"] = "TARGET_VALIDATED_LONG_CONTINUOUS"
    elif float(metrics.get("geometric_daily_growth", -1.0)) > 0.0:
        decision["status"] = "POSITIVE_COMPONENT_FULL_SYSTEM_GATE_FAIL"
    else:
        decision["status"] = "LONG_LOGIC_FAIL_COMPONENT_RETIRED"
    write_json(output / "FINAL_DECISION.json", decision)
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0 if decision["status"] != "IMPLEMENTATION_ERROR" else 2


if __name__ == "__main__":
    raise SystemExit(main())
