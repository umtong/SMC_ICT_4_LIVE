#!/usr/bin/env python3
"""Run V34 with only its liquidity-context generator replaced by V36."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--cache", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
known, _ = parser.parse_known_args()

base_path = (
    Path(__file__).resolve().parents[1]
    / "v34_extreme_absorption"
    / "run_experiment.py"
)
spec = importlib.util.spec_from_file_location("candidate09_v34_runner", base_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load v34 runner: {base_path}")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

base_configured = runner.configured
runner.VARIANTS = {"baseline": False, "with-swings": True}


def configured(base, *, require_absorption: bool, calendar_days: int):
    cfg = base_configured(
        base,
        require_absorption=True,
        calendar_days=calendar_days,
    )
    cfg["execution_seed"] = 360036
    cfg["strategy"].update(
        {
            "candidate36_include_confirmed_swings": bool(require_absorption),
            "candidate36_enable_15m": True,
            "candidate36_enable_60m": True,
            "candidate36_enable_daily": True,
        }
    )
    return cfg


runner.configured = configured
exit_code = runner.main()
path = known.output.resolve() / "FINAL_DECISION.json"
if path.exists():
    decision = json.loads(path.read_text(encoding="utf-8"))
    decision["candidate"] = "candidate-09-v36-completed-auction-absorption"
    decision["source_lineage"] = {
        "state_and_execution": "frozen v34 extreme absorption, later initiative, and pullback",
        "changed_context": "completed 15m, 60m, and daily auction extremes replace two-bar pivots",
        "exact_ablation": "with-swings re-enables confirmed pivots only",
        "known_v34_control": "2 trades; 0 wins; -5.6411% in August 2024",
    }
    path.write_text(
        json.dumps(decision, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(decision, indent=2, sort_keys=True))
raise SystemExit(exit_code)
