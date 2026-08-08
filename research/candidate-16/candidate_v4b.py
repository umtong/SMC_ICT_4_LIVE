#!/usr/bin/env python3
"""Candidate 16 v4b runner: v4 economics with repaired L1 timestamp units."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import candidate_v4 as v4_runner
import backtest as candidate05_backtest
from features_v4 import DATASET_COMMIT
from features_v4 import DATASET_SHA256
from features_v4b import load_range as load_range_v4b
from smc_ict_4.manifest import write_json_atomic


candidate05_backtest.load_range = load_range_v4b


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    result = v4_runner.run_stage(args)
    candidate = "candidate-16-v4b-l1-time-unit-fix"
    result["candidate"] = candidate
    result["implementation_fix"] = {
        "scope": "L1 Parquet timestamp unit alignment only",
        "economic_rules_changed": False,
        "from": "Arrow/Parquet native timestamp integer unit",
        "to": "explicit UTC nanoseconds before minute-key join",
    }
    write_json_atomic(args.output.resolve() / "metrics.json", result)

    run_path = args.output.resolve() / "run.json"
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_payload["candidate"] = candidate
    run_payload["candidate16_v4b"] = {
        "economic_rules_changed": False,
        "strategy_path": "research/candidate-16/strategy_v4.py",
        "feature_path": "research/candidate-16/features_v4b.py",
        "dataset_commit": DATASET_COMMIT,
        "dataset_sha256": DATASET_SHA256,
        "implementation_fix": "explicit nanosecond normalization before L1 join",
    }
    write_json_atomic(run_path, run_payload)

    contract_path = args.output.resolve() / "candidate16_v4_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["candidate"] = candidate
    contract["implementation_fix"] = {
        "economic_rules_changed": False,
        "root_cause": (
            "Parquet timestamp resolution could remain microseconds; direct "
            "integer conversion was compared with nanosecond Binance keys"
        ),
        "repair": "convert timezone-aware timestamps to ns before int64 join keys",
        "fail_closed_join_coverage": 0.95,
    }
    write_json_atomic(
        args.output.resolve() / "candidate16_v4b_contract.json",
        contract,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("--config", type=Path, required=True)
    stage.add_argument("--build-start", required=True)
    stage.add_argument("--build-end", required=True)
    stage.add_argument("--evaluation-start", required=True)
    stage.add_argument("--evaluation-end", required=True)
    stage.add_argument("--cache", type=Path, required=True)
    stage.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_stage(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
