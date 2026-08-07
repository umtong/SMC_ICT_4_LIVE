"""Deterministic NautilusTrader validation for delayed boundary reacceptance.

The fixed protocol is:

1. run BTC screen-01 with the full initial-initiative -> reclaim -> reacceptance sequence;
2. run the remaining two fixed BTC weeks only after the first-week gate;
3. after a clean base logic failure, remove only the initial initiative-state strength requirement
   once while retaining direction, reclaim, second initiative reacceptance, target, stop, costs and
   execution contracts; and
4. never promote the diagnostic result directly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
from typing import Any, Callable, Mapping

from aggtrade_delayed_reacceptance_signals_v2 import (
    ABLATION_INITIAL_MODE,
    BASE_INITIAL_MODE,
    IMPLEMENTATION_REVISION,
    REACCEPTANCE_FAMILY,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION


PROTOCOL_REVISION = "DELAYED_REACCEPTANCE_STAGED_VALIDATION_V1"
FIRST_NAME = "first-v1"
SCREEN_NAME = "screen-v1"
BASE_MODE = BASE_INITIAL_MODE
DIAGNOSTIC_MODE = ABLATION_INITIAL_MODE


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _summary_ref(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    summary = _load_json(path)
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "suite": summary.get("suite"),
        "implementation_revision": summary.get("implementation_revision"),
        "delayed_reacceptance_initial_mode": summary.get(
            "delayed_reacceptance_initial_mode"
        ),
        "suite_gate_passed": bool(summary.get("suite_gate_passed", False)),
        "closed_trades": int(summary.get("closed_trades", 0)),
        "wins": int(summary.get("wins", 0)),
        "combined_daily_geometric_growth": float(
            summary.get("combined_daily_geometric_growth", 0.0)
        ),
        "scenario_family_results": summary.get("scenario_family_results", {}),
        "trade_path_diagnostic_summary": summary.get(
            "trade_path_diagnostic_summary", {}
        ),
    }


def clear_outputs(root: Path) -> None:
    for name in (
        FIRST_NAME,
        SCREEN_NAME,
        "first-diagnostic-remove-initial-v1",
        "screen-diagnostic-remove-initial-v1",
    ):
        path = root / name
        if path.exists():
            shutil.rmtree(path)
    for name in ("stage_decision.json", "ablation_decision.json"):
        (root / name).unlink(missing_ok=True)


def validate_base_summary(
    summary: Mapping[str, Any],
    *,
    expected_suite: str,
) -> tuple[str, ...]:
    errors: list[str] = []
    if str(summary.get("suite")) != expected_suite:
        errors.append("SUITE_NOT_EXACT")
    if str(summary.get("implementation_revision")) != IMPLEMENTATION_REVISION:
        errors.append("IMPLEMENTATION_REVISION_NOT_EXACT")
    if str(summary.get("delayed_reacceptance_initial_mode")) != BASE_MODE:
        errors.append("BASE_INITIAL_MODE_NOT_EXACT")
    if bool(summary.get("diagnostic_initial_ablation", False)):
        errors.append("BASE_MARKED_DIAGNOSTIC")
    if not bool(summary.get("promotable", False)):
        errors.append("BASE_NOT_PROMOTABLE")
    if str(summary.get("single_scenario_family")) != REACCEPTANCE_FAMILY:
        errors.append("SCENARIO_FAMILY_NOT_EXACT")
    if not bool(summary.get("single_family_attribution_passed", False)):
        errors.append("SINGLE_FAMILY_ATTRIBUTION_FAILED")
    if not bool(summary.get("scenario_attribution_passed", False)):
        errors.append("SCENARIO_ATTRIBUTION_FAILED")
    if str(summary.get("ten_second_cadence_contract")) != "EXACT_CONSECUTIVE_10_SECONDS":
        errors.append("TEN_SECOND_CADENCE_NOT_EXACT")
    if str(summary.get("trade_path_diagnostic_revision")) != DIAGNOSTIC_REVISION:
        errors.append("PATH_DIAGNOSTIC_REVISION_NOT_EXACT")

    checks = summary.get("suite_gate_checks", {})
    if not isinstance(checks, Mapping):
        errors.append("SUITE_GATE_CHECKS_MISSING")
    else:
        for key in (
            "complete_auction_scenario_attribution",
            "single_delayed_reacceptance_family_attributed",
            "complete_post_run_trade_path_diagnostics",
            "base_initial_initiative_required",
        ):
            if checks.get(key) is not True:
                errors.append(f"EVIDENCE_{key.upper()}_FAILED")

    families = summary.get("scenario_family_results", {})
    if not isinstance(families, Mapping) or REACCEPTANCE_FAMILY not in families:
        errors.append("REACCEPTANCE_FAMILY_RESULTS_MISSING")
    path_summary = summary.get("trade_path_diagnostic_summary", {})
    if not isinstance(path_summary, Mapping):
        errors.append("TRADE_PATH_DIAGNOSTIC_SUMMARY_MISSING")
    else:
        closed = int(summary.get("closed_trades", 0))
        if int(path_summary.get("records", -1)) != closed:
            errors.append("PATH_DIAGNOSTIC_RECORD_COUNT_MISMATCH")
        if int(path_summary.get("complete_records", -1)) != closed:
            errors.append("PATH_DIAGNOSTIC_COMPLETE_COUNT_MISMATCH")
        if dict(path_summary.get("diagnostic_revision_counts", {})) != {
            DIAGNOSTIC_REVISION: closed
        }:
            errors.append("PATH_DIAGNOSTIC_REVISION_COUNT_MISMATCH")
    return tuple(errors)


def run_suite_process(
    *,
    python_executable: str,
    runner: Path,
    suite: str,
    initial_mode: str,
    config: Path,
    pattern_config: Path,
    output: Path,
    data_cache: Path,
    reuse_first_dir: Path | None = None,
) -> int:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    _write_json(
        output / "request.json",
        {
            "protocol_revision": PROTOCOL_REVISION,
            "implementation_revision": IMPLEMENTATION_REVISION,
            "suite": suite,
            "delayed_reacceptance_initial_mode": initial_mode,
            "diagnostic_only": initial_mode != BASE_MODE,
            "promotable": initial_mode == BASE_MODE,
            "reuse_first_dir": None if reuse_first_dir is None else str(reuse_first_dir),
        },
    )
    command = [
        python_executable,
        str(runner),
        "--suite",
        suite,
        "--ablation",
        "none",
        "--config",
        str(config),
        "--pattern-config",
        str(pattern_config),
        "--output",
        str(output),
        "--data-cache",
        str(data_cache),
    ]
    if reuse_first_dir is not None:
        command.extend(("--reuse-first-dir", str(reuse_first_dir)))
    environment = dict(os.environ)
    environment["DELAYED_REACCEPTANCE_INITIAL_MODE"] = initial_mode
    environment["DELAYED_REACCEPTANCE_CONFIG_PATH"] = str(config)
    with (output / "runner.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=environment,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
        status = int(process.wait())
    (output / "runner_exit_status.txt").write_text(f"{status}\n", encoding="utf-8")
    return status


RunSuite = Callable[..., int]


def evaluate_diagnostic(
    summary: Mapping[str, Any],
    *,
    expected_suite: str,
) -> dict[str, Any]:
    raw_checks = summary.get("suite_gate_checks", {})
    checks_are_mapping = isinstance(raw_checks, Mapping)
    checks = dict(raw_checks) if checks_are_mapping else {}
    excluded = {
        "base_contract_includes_both_auction_families",
        "base_contract_includes_both_flow_response_families",
        "base_initial_initiative_required",
    }
    economic = {key: bool(value) for key, value in checks.items() if key not in excluded}
    path_summary = summary.get("trade_path_diagnostic_summary", {})
    closed = int(summary.get("closed_trades", 0))
    evidence = {
        "suite_exact": str(summary.get("suite")) == expected_suite,
        "implementation_revision_exact": (
            str(summary.get("implementation_revision")) == IMPLEMENTATION_REVISION
        ),
        "initial_mode_exact": (
            str(summary.get("delayed_reacceptance_initial_mode"))
            == DIAGNOSTIC_MODE
        ),
        "diagnostic_flag_true": bool(summary.get("diagnostic_initial_ablation", False)),
        "not_promotable": not bool(summary.get("promotable", True)),
        "suite_gate_closed": not bool(summary.get("suite_gate_passed", True)),
        "single_family_attributed": bool(
            summary.get("single_family_attribution_passed", False)
        ),
        "scenario_attribution_complete": bool(
            summary.get("scenario_attribution_passed", False)
        ),
        "checks_are_mapping": checks_are_mapping,
        "path_summary_present": isinstance(path_summary, Mapping),
        "path_record_count_exact": (
            isinstance(path_summary, Mapping)
            and int(path_summary.get("records", -1)) == closed
        ),
        "path_complete_count_exact": (
            isinstance(path_summary, Mapping)
            and int(path_summary.get("complete_records", -1)) == closed
        ),
        "economic_check_set_nonempty": len(economic) >= 5,
    }
    evidence_passed = all(evidence.values())
    economic_passed = bool(economic) and all(economic.values())
    return {
        "expected_suite": expected_suite,
        "expected_initial_mode": DIAGNOSTIC_MODE,
        "evidence_contract_checks": evidence,
        "evidence_contract_passed": evidence_passed,
        "economic_checks": economic,
        "economic_checks_passed": economic_passed,
        "new_base_rebuild_supported": evidence_passed and economic_passed,
        "promotion_permitted": False,
    }


def execute_staged_validation(
    *,
    config: Path,
    pattern_config: Path,
    root: Path,
    data_cache: Path,
    runner: Path,
    python_executable: str = sys.executable,
    run_suite: RunSuite = run_suite_process,
) -> tuple[int, dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    clear_outputs(root)
    first_output = root / FIRST_NAME
    screen_output = root / SCREEN_NAME
    decision_path = root / "stage_decision.json"
    decision: dict[str, Any] = {
        "candidate": "candidate-08-delayed-reacceptance-btc-nautilus-v1",
        "protocol_revision": PROTOCOL_REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "base_initial_mode": BASE_MODE,
        "first_runner_status": None,
        "first_gate_passed": False,
        "screen_runner_status": None,
        "screen_gate_passed": False,
        "ablation_selected": False,
        "ablation_runner_status": None,
        "ablation_new_base_rebuild_supported": False,
        "promotion_permitted_from_ablation": False,
        "decision": "IMPLEMENTATION_FAILURE",
    }
    try:
        first_status = run_suite(
            python_executable=python_executable,
            runner=runner,
            suite="first",
            initial_mode=BASE_MODE,
            config=config,
            pattern_config=pattern_config,
            output=first_output,
            data_cache=data_cache,
            reuse_first_dir=None,
        )
        decision["first_runner_status"] = first_status
        first_summary_path = first_output / "suite_metrics.json"
        decision["first_summary"] = _summary_ref(first_summary_path)
        if first_status != 0 or not first_summary_path.exists():
            decision["decision"] = "FIRST_WEEK_IMPLEMENTATION_FAILURE"
            _write_json(decision_path, decision)
            return 1, decision
        first_summary = _load_json(first_summary_path)
        first_errors = validate_base_summary(first_summary, expected_suite="first")
        decision["first_evidence_contract_errors"] = list(first_errors)
        if first_errors:
            decision["decision"] = "FIRST_WEEK_EVIDENCE_CONTRACT_FAILURE"
            _write_json(decision_path, decision)
            return 1, decision

        first_passed = bool(first_summary.get("suite_gate_passed", False))
        decision["first_gate_passed"] = first_passed
        failed_summary = first_summary
        failed_suite = "first"
        if first_passed:
            screen_status = run_suite(
                python_executable=python_executable,
                runner=runner,
                suite="screen",
                initial_mode=BASE_MODE,
                config=config,
                pattern_config=pattern_config,
                output=screen_output,
                data_cache=data_cache,
                reuse_first_dir=first_output,
            )
            decision["screen_runner_status"] = screen_status
            screen_summary_path = screen_output / "suite_metrics.json"
            decision["screen_summary"] = _summary_ref(screen_summary_path)
            if screen_status != 0 or not screen_summary_path.exists():
                decision["decision"] = "SCREEN_IMPLEMENTATION_FAILURE"
                _write_json(decision_path, decision)
                return 1, decision
            screen_summary = _load_json(screen_summary_path)
            screen_errors = validate_base_summary(screen_summary, expected_suite="screen")
            decision["screen_evidence_contract_errors"] = list(screen_errors)
            if screen_errors:
                decision["decision"] = "SCREEN_EVIDENCE_CONTRACT_FAILURE"
                _write_json(decision_path, decision)
                return 1, decision
            screen_passed = bool(screen_summary.get("suite_gate_passed", False))
            decision["screen_gate_passed"] = screen_passed
            if screen_passed:
                decision["decision"] = "PROMOTE_TO_PREDECLARED_LONG_EVALUATION"
                _write_json(decision_path, decision)
                return 0, decision
            failed_summary = screen_summary
            failed_suite = "screen"

        decision["ablation_selected"] = True
        decision["ablation_selection"] = {
            "removed_variable": "INITIAL_INITIATIVE_RESPONSE_STATE_REQUIREMENT",
            "retained_conditions": [
                "outward_flow_direction",
                "outside_boundary_close",
                "boundary_reclaim",
                "separate_outward_initiative_reacceptance",
                "counter_auction_break",
                "structural_stop",
                "active_completed_external_target",
                "costs_and_funding",
                "shared_nav_three_percent_sizing",
            ],
            "diagnostic_initial_mode": DIAGNOSTIC_MODE,
            "promotion_permitted": False,
        }
        diagnostic_output = root / f"{failed_suite}-diagnostic-remove-initial-v1"
        diagnostic_status = run_suite(
            python_executable=python_executable,
            runner=runner,
            suite=failed_suite,
            initial_mode=DIAGNOSTIC_MODE,
            config=config,
            pattern_config=pattern_config,
            output=diagnostic_output,
            data_cache=data_cache,
            reuse_first_dir=None,
        )
        decision["ablation_runner_status"] = diagnostic_status
        diagnostic_summary_path = diagnostic_output / "suite_metrics.json"
        decision["ablation_summary"] = _summary_ref(diagnostic_summary_path)
        if diagnostic_status != 0 or not diagnostic_summary_path.exists():
            decision["decision"] = "SINGLE_ABLATION_IMPLEMENTATION_FAILURE"
            _write_json(decision_path, decision)
            return 1, decision
        evaluation = evaluate_diagnostic(
            _load_json(diagnostic_summary_path),
            expected_suite=failed_suite,
        )
        _write_json(diagnostic_output / "diagnostic_evaluation.json", evaluation)
        decision["ablation_evaluation"] = evaluation
        supported = bool(evaluation["new_base_rebuild_supported"])
        decision["ablation_new_base_rebuild_supported"] = supported
        if not bool(evaluation["evidence_contract_passed"]):
            decision["decision"] = "SINGLE_ABLATION_EVIDENCE_CONTRACT_FAILURE"
            _write_json(decision_path, decision)
            return 1, decision
        decision["decision"] = (
            "SINGLE_ABLATION_SUPPORTS_NEW_BASE_REBUILD"
            if supported
            else "SINGLE_ABLATION_FAILED_DISCARD_DELAYED_REACCEPTANCE_V1"
        )
        _write_json(decision_path, decision)
        return 0, decision
    except Exception as exc:
        decision["decision"] = "STAGED_ORCHESTRATOR_IMPLEMENTATION_FAILURE"
        decision["exception_type"] = type(exc).__name__
        decision["exception_message"] = str(exc)
        decision["traceback"] = traceback.format_exc()
        _write_json(decision_path, decision)
        return 1, decision


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pattern-config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    parser.add_argument(
        "--runner",
        type=Path,
        default=Path(__file__).resolve().parent
        / "run_aggtrade_delayed_reacceptance_nautilus.py",
    )
    args = parser.parse_args()
    status, decision = execute_staged_validation(
        config=args.config.resolve(),
        pattern_config=args.pattern_config.resolve(),
        root=args.root.resolve(),
        data_cache=args.data_cache.resolve(),
        runner=args.runner.resolve(),
    )
    print(json.dumps(decision, indent=2, sort_keys=True), flush=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
