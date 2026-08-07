#!/usr/bin/env python3
"""Materialize and verify the locked candidate-02 v105 source before data access.

The committed v105 logic/config/week are authoritative. This script derives
only the orchestration driver from the already-tested v104 data/NT driver,
replacing candidate paths and removing the old diagnostic ablation. It then
records every source Git blob in the lock before any v105 market archive is
collected or any performance is computed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path.cwd()
CANDIDATE = ROOT / "research/candidate-02"
LOCK_PATH = CANDIDATE / "v105_auction_state_lock.json"
DRIVER_PATH = CANDIDATE / "v105_first_week_driver.py"
TEMPLATE_PATH = CANDIDATE / "v104_first_week_driver.py"
WORKFLOW_PATH = ROOT / ".github/workflows/candidate-02-v105-router.yml"


def git_blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def materialize_driver() -> None:
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    text = text.replace("v104", "v105").replace("V104", "V105")
    text = text.replace(
        "candidate-02-v105-external-liquidity-common-acceptance-retest",
        "candidate-02-v105-auction-state-continuation-reversal",
    )
    replacements = {
        "2025-12-08": "2025-08-25",
        "2025-12-15": "2025-09-01",
        "2025-02-03": "2025-05-19",
        "2024-07-15": "2024-07-08",
        "20260807104": "20260807107",
        "20260807105": "20260807108",
        "20260807106": "20260807109",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    marker = '    assert lock["single_ablation"]["removed_level_family"] == "EQUAL_SWING_CLUSTER"\n'
    if text.count(marker) != 1:
        raise RuntimeError("v105 driver ablation assertion materialization mismatch")
    text = text.replace(marker, "")
    start = text.index("def publish_decision(")
    end = text.index("\ndef write_summary(", start)
    replacement = '''def publish_decision(baseline: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    baseline_pass = metric_pass(baseline)
    decision = {
        "candidate_family": "candidate-02-v105-auction-state-continuation-reversal",
        "baseline": {
            "metrics": baseline,
            "passes_all_first_week_gates": baseline_pass,
        },
        "single_precommitted_ablation": None,
        "status": (
            "FIRST_WEEK_PROMOTION_ELIGIBLE"
            if baseline_pass
            else "FIRST_WEEK_REJECT"
        ),
        "next_week_allowed": baseline_pass,
        "long_evaluation_allowed": False,
        "risk_fraction": 0.03,
        "performance_engine": "NautilusTrader 1.230.0",
        "custom_backtest_engine": False,
        "project_target_met": False,
    }
    write_json(ROOT / "artifacts-v105-first-week-decision.json", decision)
    return decision
'''
    text = text[:start] + replacement + text[end:]
    DRIVER_PATH.write_text(text, encoding="utf-8")


def source_paths() -> dict[str, Path]:
    return {
        "base_config": CANDIDATE / "v105_base_config.json",
        "core": CANDIDATE / "v105_auction_state_core.py",
        "driver": DRIVER_PATH,
        "nt_backtest": CANDIDATE / "v105_nt_backtest.py",
        "nt_strategy": CANDIDATE / "v105_nt_strategy.py",
        "runtime_materializer": CANDIDATE / "v105_runtime_materialize.py",
        "test_runner": CANDIDATE / "tests/run_v105_tests.py",
        "logic_tests": CANDIDATE / "tests/test_v105_logic.py",
        "week_selection": CANDIDATE / "v105_week_selection.json",
        "workflow": WORKFLOW_PATH,
        "driver_template_v104": TEMPLATE_PATH,
        "dependency_v104_core": CANDIDATE / "v104_external_liquidity_core.py",
        "dependency_v104_strategy": CANDIDATE / "v104_nt_strategy.py",
        "dependency_core_risk": CANDIDATE / "core.py",
        "dependency_v53_nt_core": CANDIDATE / "v53_nt_core.py",
        "dependency_v53_nt_strategy": CANDIDATE / "v53_nt_strategy.py",
        "dependency_v53_nt_backtest": CANDIDATE / "v53_nt_backtest.py",
        "dependency_v75_collect": ROOT / ".candidate-02-v75/collect_first_week.py",
        "dependency_v75_features": ROOT / ".candidate-02-v75/build_features.py",
        "dependency_v89_spot_collect": ROOT / ".candidate-02-v89/collect_spot.py",
        "dependency_v89_spot_augment": ROOT / ".candidate-02-v89/augment_cross_market.py",
    }


def update_lock() -> None:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if lock.get("status") != "LOCKED_BEFORE_FIRST_WEEK_COLLECTION":
        raise RuntimeError("v105 lock status changed before materialization")
    first = lock.get("first_week", {})
    if first.get("start_utc") != "2025-08-25T00:00:00Z":
        raise RuntimeError("v105 first week changed")
    if first.get("raw_data_status_at_lock") != "NOT_COLLECTED_FOR_V105":
        raise RuntimeError("v105 predata status changed")
    config = json.loads((CANDIDATE / "v105_base_config.json").read_text(encoding="utf-8"))
    if config.get("candidate") != "candidate-02-v105-auction-state-continuation-reversal":
        raise RuntimeError("v105 candidate changed")
    if config["validation"]["first_week_start"] != "2025-08-25":
        raise RuntimeError("v105 config week changed")
    if float(config["risk"]["risk_fraction"]) != 0.03:
        raise RuntimeError("v105 risk changed")
    paths = source_paths()
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"v105 source missing: {missing}")
    lock["source_files"] = {key: str(path.relative_to(ROOT)) for key, path in paths.items()}
    lock["source_git_blob_sha"] = {key: git_blob(path) for key, path in paths.items()}
    lock["source_materialization"] = {
        "driver_derived_from_locked_v104_orchestration": True,
        "driver_materialized_before_market_data": True,
        "equal_swing_ablation_removed_to_prioritize_central_system": True,
        "materialization_commit_does_not_retrigger_router": True,
        "trigger_commit": os.environ.get("GITHUB_SHA"),
    }
    LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify() -> None:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    for key, relative in lock.get("source_files", {}).items():
        path = ROOT / relative
        expected = lock["source_git_blob_sha"][key]
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = git_blob(path)
        if actual != expected:
            raise RuntimeError(f"v105 locked source changed for {key}: {actual} != {expected}")
    print(json.dumps({
        "driver_blob": git_blob(DRIVER_PATH),
        "locked_sources": len(lock.get("source_files", {})),
        "status": "V105_EXACT_SOURCE_VERIFIED_BEFORE_MARKET_DATA",
    }, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify()
        return 0
    materialize_driver()
    update_lock()
    verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
