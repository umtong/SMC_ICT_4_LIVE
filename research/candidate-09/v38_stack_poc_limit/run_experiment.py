#!/usr/bin/env python3
"""Compare native stack-POC limit execution with the exact V35 market control."""
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
    / "v35_completed_auction_footprint"
    / "run_experiment.py"
)
spec = importlib.util.spec_from_file_location("candidate09_v35_runner", base_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load v35 runner: {base_path}")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

base_configured = runner.configured
runner.VARIANTS = {"baseline": True, "market-control": False}


def configured(base, *, include_swings: bool, calendar_days: int):
    cfg = base_configured(
        base,
        include_swings=False,
        calendar_days=calendar_days,
    )
    cfg["execution_seed"] = 380038
    cfg["strategy"].update(
        {
            "candidate38_use_stack_limit": bool(include_swings),
            "candidate38_limit_expiry_bars": 2,
        }
    )
    return cfg


runner.configured = configured
exit_code = runner.main()
path = known.output.resolve() / "FINAL_DECISION.json"
if path.exists():
    decision = json.loads(path.read_text(encoding="utf-8"))
    decision["candidate"] = "candidate-09-v38-stack-poc-limit"
    decision["source_lineage"] = {
        "signal": "frozen V35 completed-auction footprint acceptance",
        "baseline_execution": "passive two-bar GTD limit at observed stack POC",
        "exact_control": "V35 market bracket after the same defended retest",
        "unchanged": "stop, natural target, costs, 3% current-NAV risk, dates, state logic",
    }
    path.write_text(
        json.dumps(decision, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(decision, indent=2, sort_keys=True))
raise SystemExit(exit_code)
