"""Deterministic staged NautilusTrader validation for candidate-08 auction-router v2.

The orchestrator runs exactly this predeclared sequence:

1. fixed first week with both economic families;
2. the remaining fixed weeks only if the first-week gate passes;
3. at most one diagnostic-only family ablation, selected by the frozen economic-family rule;
4. a final evidence-backed decision which never promotes an ablation result directly.

Every staged destination is deleted before execution. A prior ``suite_metrics.json`` can therefore
never stand in for a failed current run. Implementation failures return nonzero only after evidence
and the failure decision have been written.
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

from auction_diagnostic_evaluation import evaluate_diagnostic_summary
from auction_family_ablation_stage import choose_failed_stage
from auction_family_ablation_decision import IMPLEMENTATION_REVISION


BASE_MODE = "both"
FIRST_NAME = "first-v3"
SCREEN_NAME = "screen-v2"
PROTOCOL_REVISION = "AUCTION_ROUTER_STAGED_VALIDATION_V1"


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


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def clear_staged_outputs(root: Path) -> None:
    """Remove only outputs owned by this staged protocol, preserving prior research evidence."""

    for path in (root / FIRST_NAME, root / SCREEN_NAME):
        _remove_path(path)
    for pattern in ("first-ablation-*-v1", "screen-ablation-*-v1"):
        for path in root.glob(pattern):
            _remove_path(path)
    for name in ("stage_decision.json", "ablation_decision.json"):
        _remove_path(root / name)


def _summary_ref(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    summary = _load_json(path)
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "suite": summary.get("suite"),
        "auction_family_mode": summary.get("auction_family_mode"),
        "implementation_revision": summary.get("implementation_revision"),
        "suite_gate_passed": bool(summary.get("suite_gate_passed", False)),
        "closed_trades": int(summary.get("closed_trades", 0)),
        "wins": int(summary.get("wins", 0)),
        "combined_daily_geometric_growth": float(
            summary.get("combined_daily_geometric_growth", 0.0) or 0.0
        ),
        "scenario_family_results": summary.get("scenario_family_results", {}),
    }


def validate_base_summary(
    summary: Mapping[str, Any],
    *,
    expected_suite: str,
) -> tuple[str, ...]:
    """Return implementation/evidence-contract errors, never economic gate failures."""

    errors: list[str] = []
    if str(summary.get("suite")) != expected_suite:
        errors.append("SUITE_NOT_EXACT")
    if str(summary.get("implementation_revision")) != IMPLEMENTATION_REVISION:
        errors.append("IMPLEMENTATION_REVISION_NOT_EXACT")
    if str(summary.get("auction_family_mode")) != BASE_MODE:
        errors.append("BASE_FAMILY_MODE_NOT_BOTH")
    if bool(summary.get("diagnostic_family_ablation", False)):
        errors.append("BASE_MARKED_AS_DIAGNOSTIC")
    if not bool(summary.get("promotable", False)):
        errors.append("BASE_NOT_MARKED_PROMOTABLE")
    if not bool(summary.get("scenario_attribution_passed", False)):
        errors.append("SCENARIO_ATTRIBUTION_INCOMPLETE")

    checks = summary.get("scenario_attribution_checks", {})
    if not isinstance(checks, Mapping):
        errors.append("SCENARIO_ATTRIBUTION_CHECKS_MISSING")
    else:
        for key in (
            "all_signals_attributed",
            "all_closed_trades_attributed",
            "no_unclassified_signals",
            "no_unclassified_closed_trades",
        ):
            if checks.get(key) is not True:
                errors.append(f"ATTRIBUTION_{key.upper()}_FAILED")
    return tuple(errors)


def _request_payload(
    *,
    suite: str,
    family_mode: str,
    diagnostic_only: bool,
    reuse_first_dir: Path | None,
) -> dict[str, Any]:
    return {
        "suite": suite,
        "ablation": "none",
        "auction_family_mode": family_mode,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "protocol_revision": PROTOCOL_REVISION,
        "diagnostic_only": diagnostic_only,
        "promotable": not diagnostic_only,
        "reuse_first_dir": None if reuse_first_dir is None else str(reuse_first_dir),
        "scenario_contract": "auction-router-v1",
    }


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
    diagnostic_only: bool = False,
) -> int:
    """Run one exact suite while streaming output to console and immutable runner.log."""

    _remove_path(output)
    output.mkdir(parents=True, exist_ok=True)
    _write_json(
        output / "request.json",
        _request_payload(
            suite=suite,
            family_mode=family_mode,
            diagnostic_only=diagnostic_only,
            reuse_first_dir=reuse_first_dir,
        ),
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
    environment["AUCTION_ROUTER_FAMILY_MODE"] = family_mode
    log_path = output / "runner.log"
    with log_path.open("w", encoding="utf-8") as log:
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
    (output / "runner_exit_status.txt").write_text(
        f"{status}\n",
        encoding="utf-8",
    )
    return status


RunSuite = Callable[..., int]


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
    """Execute the frozen staged protocol and return process status plus final decision."""

    root.mkdir(parents=True, exist_ok=True)
    first_output = root / FIRST_NAME
    screen_output = root / SCREEN_NAME
    stage_path = root / "stage_decision.json"
    decision: dict[str, Any] = {
        "candidate": "candidate-08-auction-router-nautilus-v1",
        "implementation_revision": IMPLEMENTATION_REVISION,
        "protocol_revision": PROTOCOL_REVISION,
        "base_family_mode": BASE_MODE,
        "git_sha": os.environ.get("GITHUB_SHA"),
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
        clear_staged_outputs(root)
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
            diagnostic_only=False,
        )
        decision["first_runner_status"] = first_status
        first_summary_path = first_output / "suite_metrics.json"
        decision["first_summary"] = _summary_ref(first_summary_path)
        if first_status != 0 or not first_summary_path.exists():
            decision["decision"] = "FIRST_WEEK_IMPLEMENTATION_FAILURE"
            _write_json(stage_path, decision)
            return 1, decision

        first_summary = _load_json(first_summary_path)
        first_errors = validate_base_summary(first_summary, expected_suite="first")
        decision["first_evidence_contract_errors"] = list(first_errors)
        if first_errors:
            decision["decision"] = "FIRST_WEEK_EVIDENCE_CONTRACT_FAILURE"
            _write_json(stage_path, decision)
            return 1, decision

        first_passed = bool(first_summary.get("suite_gate_passed", False))
        decision["first_gate_passed"] = first_passed
        screen_status = ""

        if first_passed:
            screen_code = run_suite(
                python_executable=python_executable,
                runner=runner,
                suite="screen",
                family_mode=BASE_MODE,
                config=config,
                pattern_config=pattern_config,
                output=screen_output,
                data_cache=data_cache,
                reuse_first_dir=first_output,
                diagnostic_only=False,
            )
            decision["screen_runner_status"] = screen_code
            screen_status = str(screen_code)
            screen_summary_path = screen_output / "suite_metrics.json"
            decision["screen_summary"] = _summary_ref(screen_summary_path)
            if screen_code != 0 or not screen_summary_path.exists():
                decision["decision"] = "SCREEN_IMPLEMENTATION_FAILURE"
                _write_json(stage_path, decision)
                return 1, decision

            screen_summary = _load_json(screen_summary_path)
            screen_errors = validate_base_summary(screen_summary, expected_suite="screen")
            decision["screen_evidence_contract_errors"] = list(screen_errors)
            if screen_errors:
                decision["decision"] = "SCREEN_EVIDENCE_CONTRACT_FAILURE"
                _write_json(stage_path, decision)
                return 1, decision

            screen_passed = bool(screen_summary.get("suite_gate_passed", False))
            decision["screen_gate_passed"] = screen_passed
            if screen_passed:
                decision["decision"] = "PROMOTE_TO_PREDECLARED_LONG_EVALUATION"
                _write_json(stage_path, decision)
                return 0, decision

        selector = choose_failed_stage(
            root=root,
            first_output=first_output,
            screen_output=screen_output,
            first_passed=first_passed,
            screen_status=screen_status,
        )
        _write_json(root / "ablation_decision.json", selector)
        decision["ablation_selection"] = selector
        decision["ablation_selected"] = bool(selector["selected"])
        if not selector["selected"]:
            decision["decision"] = (
                "FIRST_WEEK_LOGIC_FAILURE_NO_VALID_FAMILY_ABLATION_DISCARD_ROUTER_V1"
                if not first_passed
                else "SCREEN_LOGIC_FAILURE_NO_VALID_FAMILY_ABLATION_DISCARD_ROUTER_V1"
            )
            _write_json(stage_path, decision)
            return 0, decision

        ablation_output = Path(str(selector["output"]))
        family_mode = str(selector["family_mode"])
        suite = str(selector["suite"])
        ablation_status = run_suite(
            python_executable=python_executable,
            runner=runner,
            suite=suite,
            family_mode=family_mode,
            config=config,
            pattern_config=pattern_config,
            output=ablation_output,
            data_cache=data_cache,
            reuse_first_dir=None,
            diagnostic_only=True,
        )
        decision["ablation_runner_status"] = ablation_status
        ablation_summary_path = ablation_output / "suite_metrics.json"
        decision["ablation_summary"] = _summary_ref(ablation_summary_path)
        if ablation_status != 0 or not ablation_summary_path.exists():
            decision["decision"] = "DIAGNOSTIC_ABLATION_IMPLEMENTATION_FAILURE"
            _write_json(stage_path, decision)
            return 1, decision

        evaluation = evaluate_diagnostic_summary(
            _load_json(ablation_summary_path),
            expected_mode=family_mode,
        )
        _write_json(ablation_output / "diagnostic_evaluation.json", evaluation)
        decision["ablation_evaluation"] = evaluation
        supported = bool(evaluation["new_base_rebuild_supported"])
        decision["ablation_new_base_rebuild_supported"] = supported
        decision["promotion_permitted_from_ablation"] = False
        if supported:
            decision["decision"] = (
                "FIRST_WEEK_ABLATION_SUPPORTS_NEW_SINGLE_FAMILY_BASE_REBUILD"
                if suite == "first"
                else "SCREEN_ABLATION_SUPPORTS_NEW_SINGLE_FAMILY_BASE_REBUILD"
            )
        else:
            decision["decision"] = (
                "FIRST_WEEK_ABLATION_FAILED_DISCARD_ROUTER_V1"
                if suite == "first"
                else "SCREEN_ABLATION_FAILED_DISCARD_ROUTER_V1"
            )
        _write_json(stage_path, decision)
        return 0, decision
    except Exception as exc:  # evidence first, then propagate implementation failure
        decision["decision"] = "STAGED_ORCHESTRATOR_IMPLEMENTATION_FAILURE"
        decision["exception_type"] = type(exc).__name__
        decision["exception_message"] = str(exc)
        decision["traceback"] = traceback.format_exc()
        _write_json(stage_path, decision)
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
        / "run_aggtrade_auction_router_nautilus_v2.py",
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
