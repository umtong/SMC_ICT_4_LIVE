"""Deterministic staged NautilusTrader validation for the flow-response auction candidate.

The protocol is fixed before performance evidence:

1. run the fixed BTC first week with both economic families;
2. run the remaining two fixed BTC weeks only after a first-week pass;
3. after a clean base logic failure, permit at most one predeclared family diagnostic;
4. never promote a diagnostic result directly; it may only support rebuilding a new base.

Implementation and evidence-contract failures return nonzero. Clean economic failures and discard
decisions return zero after evidence is written, so CI status does not mislabel a valid negative
experiment as broken code or broken code as an economic result.
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

from aggtrade_flow_response_auction_signals_v2 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)
from flow_response_diagnostic_evaluation import evaluate_diagnostic_summary
from flow_response_family_ablation_decision import select_single_family_ablation


PROTOCOL_REVISION = "FLOW_RESPONSE_STAGED_VALIDATION_V1"
FIRST_NAME = "first-v1"
SCREEN_NAME = "screen-v1"
BASE_MODE = "both"
_BASE_EVIDENCE_SUITE_CHECKS = (
    "complete_auction_scenario_attribution",
    "complete_post_run_trade_path_diagnostics",
    "base_contract_includes_both_auction_families",
    "base_contract_includes_both_flow_response_families",
)


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
        "flow_response_family_mode": summary.get("flow_response_family_mode"),
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


def clear_staged_outputs(root: Path) -> None:
    """Remove only outputs owned by this exact staged protocol."""

    for name in (
        FIRST_NAME,
        SCREEN_NAME,
        "first-diagnostic-initiative-v1",
        "first-diagnostic-absorption-v1",
        "screen-diagnostic-initiative-v1",
        "screen-diagnostic-absorption-v1",
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
    """Return only implementation/evidence errors, never economic gate failures."""

    errors: list[str] = []
    if str(summary.get("suite")) != expected_suite:
        errors.append("SUITE_NOT_EXACT")
    if str(summary.get("implementation_revision")) != IMPLEMENTATION_REVISION:
        errors.append("IMPLEMENTATION_REVISION_NOT_EXACT")
    mode = str(
        summary.get(
            "flow_response_family_mode",
            summary.get("auction_family_mode", ""),
        )
    )
    if mode != BASE_MODE:
        errors.append("BASE_FAMILY_MODE_NOT_BOTH")
    if bool(summary.get("diagnostic_family_ablation", False)):
        errors.append("BASE_MARKED_AS_DIAGNOSTIC")
    if not bool(summary.get("promotable", False)):
        errors.append("BASE_NOT_MARKED_PROMOTABLE")
    if not bool(summary.get("scenario_attribution_passed", False)):
        errors.append("SCENARIO_ATTRIBUTION_INCOMPLETE")

    attribution_checks = summary.get("scenario_attribution_checks", {})
    if not isinstance(attribution_checks, Mapping):
        errors.append("SCENARIO_ATTRIBUTION_CHECKS_MISSING")
    else:
        for key in (
            "all_signals_attributed",
            "all_closed_trades_attributed",
            "no_unclassified_signals",
            "no_unclassified_closed_trades",
        ):
            if attribution_checks.get(key) is not True:
                errors.append(f"ATTRIBUTION_{key.upper()}_FAILED")

    suite_checks = summary.get("suite_gate_checks", {})
    if not isinstance(suite_checks, Mapping):
        errors.append("SUITE_GATE_CHECKS_MISSING")
    else:
        for key in _BASE_EVIDENCE_SUITE_CHECKS:
            if suite_checks.get(key) is not True:
                errors.append(f"EVIDENCE_{key.upper()}_FAILED")

    families = summary.get("scenario_family_results", {})
    if not isinstance(families, Mapping) or set(families) != {
        INITIATIVE_FAMILY,
        ABSORPTION_FAMILY,
    }:
        errors.append("SCENARIO_FAMILY_SET_NOT_EXACT")

    path_summary = summary.get("trade_path_diagnostic_summary", {})
    if not isinstance(path_summary, Mapping):
        errors.append("TRADE_PATH_DIAGNOSTIC_SUMMARY_MISSING")
    else:
        closed_trades = int(summary.get("closed_trades", 0))
        if int(path_summary.get("records", -1)) != closed_trades:
            errors.append("TRADE_PATH_RECORD_COUNT_MISMATCH")
        if int(path_summary.get("complete_records", -1)) != closed_trades:
            errors.append("TRADE_PATH_COMPLETE_COUNT_MISMATCH")
    return tuple(errors)


def run_suite_process(
    *,
    python_executable: str,
    runner: Path,
    suite: str,
    family_mode: str,
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
            "flow_response_family_mode": family_mode,
            "diagnostic_only": family_mode != BASE_MODE,
            "promotable": family_mode == BASE_MODE,
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
    environment["FLOW_RESPONSE_AUCTION_FAMILY_MODE"] = family_mode
    environment["FLOW_RESPONSE_AUCTION_CONFIG_PATH"] = str(config)
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


def _diagnostic_output(root: Path, *, suite: str, mode: str) -> Path:
    family = "initiative" if mode == "initiative_only" else "absorption"
    return root / f"{suite}-diagnostic-{family}-v1"


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
    clear_staged_outputs(root)
    first_output = root / FIRST_NAME
    screen_output = root / SCREEN_NAME
    decision_path = root / "stage_decision.json"
    decision: dict[str, Any] = {
        "candidate": "candidate-08-flow-response-auction-btc-nautilus-v1",
        "protocol_revision": PROTOCOL_REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "base_family_mode": BASE_MODE,
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
            family_mode=BASE_MODE,
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
                family_mode=BASE_MODE,
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

        ablation = select_single_family_ablation(failed_summary)
        ablation_payload = ablation.to_dict()
        _write_json(root / "ablation_decision.json", ablation_payload)
        decision["ablation_selection"] = ablation_payload
        decision["ablation_selected"] = ablation.selected
        if not ablation.selected or ablation.family_mode is None:
            decision["decision"] = (
                "FIRST_WEEK_LOGIC_FAILURE_DISCARD_FLOW_RESPONSE_V2"
                if failed_suite == "first"
                else "THREE_WEEK_LOGIC_FAILURE_DISCARD_FLOW_RESPONSE_V2"
            )
            _write_json(decision_path, decision)
            return 0, decision

        diagnostic_output = _diagnostic_output(
            root,
            suite=failed_suite,
            mode=ablation.family_mode,
        )
        diagnostic_status = run_suite(
            python_executable=python_executable,
            runner=runner,
            suite=failed_suite,
            family_mode=ablation.family_mode,
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
            decision["decision"] = "DIAGNOSTIC_ABLATION_IMPLEMENTATION_FAILURE"
            _write_json(decision_path, decision)
            return 1, decision

        evaluation = evaluate_diagnostic_summary(
            _load_json(diagnostic_summary_path),
            expected_mode=ablation.family_mode,
        )
        _write_json(diagnostic_output / "diagnostic_evaluation.json", evaluation)
        decision["ablation_evaluation"] = evaluation
        supported = bool(evaluation["new_base_rebuild_supported"])
        decision["ablation_new_base_rebuild_supported"] = supported
        decision["promotion_permitted_from_ablation"] = False
        if not bool(evaluation["evidence_contract_passed"]):
            decision["decision"] = "DIAGNOSTIC_ABLATION_EVIDENCE_CONTRACT_FAILURE"
            _write_json(decision_path, decision)
            return 1, decision
        if supported:
            decision["decision"] = (
                "FIRST_WEEK_ABLATION_SUPPORTS_NEW_SINGLE_FAMILY_BASE_REBUILD"
                if failed_suite == "first"
                else "SCREEN_ABLATION_SUPPORTS_NEW_SINGLE_FAMILY_BASE_REBUILD"
            )
        else:
            decision["decision"] = (
                "FIRST_WEEK_ABLATION_FAILED_DISCARD_FLOW_RESPONSE_V2"
                if failed_suite == "first"
                else "SCREEN_ABLATION_FAILED_DISCARD_FLOW_RESPONSE_V2"
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
        / "run_aggtrade_flow_response_auction_nautilus.py",
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
