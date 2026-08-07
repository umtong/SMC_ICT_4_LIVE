"""Deterministic NautilusTrader validation for the intrinsic repricing successor.

The protocol is frozen before performance evidence:

1. run the fixed BTC first week with both entry paths;
2. run the remaining two fixed BTC weeks only after the first-week gate;
3. after a clean base logic failure, permit one path-removal diagnostic only when both paths traded
   independently and one is cost-after positive while the other is negative; and
4. never promote diagnostic evidence directly.

Implementation and evidence-contract failures return nonzero.  Clean economic failures write a
terminal decision and return zero.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
from typing import Any, Callable, Mapping

from aggtrade_intrinsic_repricing_signals import (
    DIRECT_PERSISTENCE_PATH,
    IMPLEMENTATION_REVISION,
    INTRINSIC_REPRICING_FAMILY,
    REPRICE_RESUMPTION_PATH,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION


PROTOCOL_REVISION = "INTRINSIC_REPRICING_STAGED_VALIDATION_V1"
FIRST_NAME = "first-v1"
SCREEN_NAME = "screen-v1"
BASE_MODE = "both_paths"
PATH_TO_MODE = {
    DIRECT_PERSISTENCE_PATH: "direct_only",
    REPRICE_RESUMPTION_PATH: "reprice_only",
}


@dataclass(frozen=True, slots=True)
class PathContribution:
    path: str
    signals: int
    closed_trades: int
    wins: int
    realized_pnl_usdt: float


@dataclass(frozen=True, slots=True)
class PathAblationDecision:
    selected: bool
    reason: str
    suite: str
    path_mode: str | None
    retained_path: str | None
    removed_path: str | None
    contributions: tuple[PathContribution, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        "intrinsic_repricing_path_mode": summary.get("intrinsic_repricing_path_mode"),
        "suite_gate_passed": bool(summary.get("suite_gate_passed", False)),
        "closed_trades": int(summary.get("closed_trades", 0)),
        "wins": int(summary.get("wins", 0)),
        "combined_daily_geometric_growth": float(
            summary.get("combined_daily_geometric_growth", 0.0)
        ),
        "entry_path_results": summary.get("entry_path_results", {}),
        "trade_path_diagnostic_summary": summary.get(
            "trade_path_diagnostic_summary", {}
        ),
    }


def clear_outputs(root: Path) -> None:
    for name in (
        FIRST_NAME,
        SCREEN_NAME,
        "first-diagnostic-direct-v1",
        "first-diagnostic-reprice-v1",
        "screen-diagnostic-direct-v1",
        "screen-diagnostic-reprice-v1",
    ):
        path = root / name
        if path.exists():
            shutil.rmtree(path)
    for name in ("stage_decision.json", "path_ablation_decision.json"):
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
    if str(summary.get("intrinsic_repricing_path_mode")) != BASE_MODE:
        errors.append("BASE_PATH_MODE_NOT_BOTH")
    if bool(summary.get("diagnostic_path_ablation", False)):
        errors.append("BASE_MARKED_DIAGNOSTIC")
    if not bool(summary.get("promotable", False)):
        errors.append("BASE_NOT_PROMOTABLE")
    if str(summary.get("single_scenario_family")) != INTRINSIC_REPRICING_FAMILY:
        errors.append("SCENARIO_FAMILY_NOT_EXACT")
    if not bool(summary.get("single_family_attribution_passed", False)):
        errors.append("SINGLE_FAMILY_ATTRIBUTION_FAILED")
    if not bool(summary.get("entry_path_attribution_passed", False)):
        errors.append("ENTRY_PATH_ATTRIBUTION_FAILED")
    if str(summary.get("ten_second_cadence_contract")) != "EXACT_CONSECUTIVE_10_SECONDS":
        errors.append("TEN_SECOND_CADENCE_NOT_EXACT")
    if str(summary.get("trade_path_diagnostic_revision")) != DIAGNOSTIC_REVISION:
        errors.append("PATH_DIAGNOSTIC_REVISION_NOT_EXACT")

    checks = summary.get("suite_gate_checks", {})
    if not isinstance(checks, Mapping):
        errors.append("SUITE_GATE_CHECKS_MISSING")
    else:
        for key in (
            "single_intrinsic_repricing_family_attributed",
            "complete_intrinsic_entry_path_attribution",
            "complete_post_run_trade_path_diagnostics",
            "base_contract_includes_both_entry_paths",
        ):
            if checks.get(key) is not True:
                errors.append(f"EVIDENCE_{key.upper()}_FAILED")

    path_results = summary.get("entry_path_results", {})
    if not isinstance(path_results, Mapping) or set(path_results) != {
        DIRECT_PERSISTENCE_PATH,
        REPRICE_RESUMPTION_PATH,
    }:
        errors.append("ENTRY_PATH_RESULT_SET_NOT_EXACT")
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


def select_path_ablation(summary: Mapping[str, Any]) -> PathAblationDecision:
    suite = str(summary.get("suite", "unknown"))
    raw = summary.get("entry_path_results", {})
    if suite not in {"first", "screen"}:
        return PathAblationDecision(False, "INVALID_SUITE", suite, None, None, None, ())
    if validate_base_summary(summary, expected_suite=suite):
        return PathAblationDecision(
            False,
            "BASE_EVIDENCE_CONTRACT_INVALID",
            suite,
            None,
            None,
            None,
            (),
        )
    if bool(summary.get("suite_gate_passed", False)):
        return PathAblationDecision(False, "BASE_GATE_ALREADY_PASSED", suite, None, None, None, ())
    contributions = tuple(
        PathContribution(
            path=path,
            signals=int(raw[path].get("signals", 0)),
            closed_trades=int(raw[path].get("closed_trades", 0)),
            wins=int(raw[path].get("wins", 0)),
            realized_pnl_usdt=float(raw[path].get("realized_pnl_usdt", 0.0)),
        )
        for path in (DIRECT_PERSISTENCE_PATH, REPRICE_RESUMPTION_PATH)
    )
    if sum(item.closed_trades for item in contributions) != int(
        summary.get("closed_trades", 0)
    ):
        return PathAblationDecision(
            False,
            "PATH_CLOSED_TRADE_COUNT_MISMATCH",
            suite,
            None,
            None,
            None,
            contributions,
        )
    positive = [
        item
        for item in contributions
        if item.closed_trades >= 1 and item.realized_pnl_usdt > 0.0
    ]
    negative = [
        item
        for item in contributions
        if item.closed_trades >= 1 and item.realized_pnl_usdt < 0.0
    ]
    if len(positive) != 1 or len(negative) != 1:
        if all(item.closed_trades == 0 for item in contributions):
            reason = "NO_EXECUTED_PATH_OPPORTUNITY"
        elif len(negative) == 2:
            reason = "BOTH_ENTRY_PATHS_ECONOMICALLY_NEGATIVE"
        elif len(positive) == 2:
            reason = "NO_DESTRUCTIVE_PATH_TO_ABLATE"
        elif any(item.closed_trades == 0 for item in contributions):
            reason = "BOTH_PATHS_NOT_INDEPENDENTLY_EXECUTED"
        else:
            reason = "ENTRY_PATH_CONTRIBUTIONS_NOT_CLEANLY_SEPARATED"
        return PathAblationDecision(
            False,
            reason,
            suite,
            None,
            None,
            None,
            contributions,
        )
    retained = positive[0]
    removed = negative[0]
    return PathAblationDecision(
        True,
        "ONE_POSITIVE_AND_ONE_NEGATIVE_ENTRY_PATH",
        suite,
        PATH_TO_MODE[retained.path],
        retained.path,
        removed.path,
        contributions,
    )


def _diagnostic_output(root: Path, *, suite: str, mode: str) -> Path:
    label = "direct" if mode == "direct_only" else "reprice"
    return root / f"{suite}-diagnostic-{label}-v1"


def run_suite_process(
    *,
    python_executable: str,
    runner: Path,
    suite: str,
    path_mode: str,
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
            "intrinsic_repricing_path_mode": path_mode,
            "diagnostic_only": path_mode != BASE_MODE,
            "promotable": path_mode == BASE_MODE,
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
    environment["INTRINSIC_REPRICING_PATH_MODE"] = path_mode
    environment["INTRINSIC_REPRICING_CONFIG_PATH"] = str(config)
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


def _diagnostic_evaluation(
    summary: Mapping[str, Any],
    *,
    expected_mode: str,
) -> dict[str, Any]:
    checks = dict(summary.get("suite_gate_checks", {}))
    excluded = {
        "base_contract_includes_both_auction_families",
        "base_contract_includes_both_flow_response_families",
        "base_contract_includes_both_entry_paths",
    }
    economic = {key: bool(value) for key, value in checks.items() if key not in excluded}
    evidence = {
        "implementation_revision_exact": (
            str(summary.get("implementation_revision")) == IMPLEMENTATION_REVISION
        ),
        "path_mode_exact": str(summary.get("intrinsic_repricing_path_mode")) == expected_mode,
        "diagnostic_flag_true": bool(summary.get("diagnostic_path_ablation", False)),
        "not_promotable": not bool(summary.get("promotable", True)),
        "suite_gate_closed": not bool(summary.get("suite_gate_passed", True)),
        "single_family_attributed": bool(
            summary.get("single_family_attribution_passed", False)
        ),
        "entry_path_attributed": bool(summary.get("entry_path_attribution_passed", False)),
        "path_diagnostics_complete": checks.get(
            "complete_post_run_trade_path_diagnostics"
        )
        is True,
        "economic_check_set_nonempty": len(economic) >= 5,
    }
    return {
        "expected_mode": expected_mode,
        "evidence_contract_checks": evidence,
        "evidence_contract_passed": all(evidence.values()),
        "economic_checks": economic,
        "economic_checks_passed": bool(economic) and all(economic.values()),
        "new_base_rebuild_supported": all(evidence.values())
        and bool(economic)
        and all(economic.values()),
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
        "candidate": "candidate-08-intrinsic-repricing-btc-nautilus-v1",
        "protocol_revision": PROTOCOL_REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "base_path_mode": BASE_MODE,
        "first_runner_status": None,
        "first_gate_passed": False,
        "screen_runner_status": None,
        "screen_gate_passed": False,
        "path_ablation_selected": False,
        "path_ablation_runner_status": None,
        "path_ablation_new_base_rebuild_supported": False,
        "promotion_permitted_from_ablation": False,
        "decision": "IMPLEMENTATION_FAILURE",
    }
    try:
        first_status = run_suite(
            python_executable=python_executable,
            runner=runner,
            suite="first",
            path_mode=BASE_MODE,
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
                path_mode=BASE_MODE,
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

        ablation = select_path_ablation(failed_summary)
        payload = ablation.to_dict()
        _write_json(root / "path_ablation_decision.json", payload)
        decision["path_ablation_selection"] = payload
        decision["path_ablation_selected"] = ablation.selected
        if not ablation.selected or ablation.path_mode is None:
            decision["decision"] = (
                "FIRST_WEEK_LOGIC_FAILURE_DISCARD_INTRINSIC_REPRICING_V1"
                if failed_suite == "first"
                else "THREE_WEEK_LOGIC_FAILURE_DISCARD_INTRINSIC_REPRICING_V1"
            )
            _write_json(decision_path, decision)
            return 0, decision

        diagnostic_output = _diagnostic_output(
            root,
            suite=failed_suite,
            mode=ablation.path_mode,
        )
        diagnostic_status = run_suite(
            python_executable=python_executable,
            runner=runner,
            suite=failed_suite,
            path_mode=ablation.path_mode,
            config=config,
            pattern_config=pattern_config,
            output=diagnostic_output,
            data_cache=data_cache,
            reuse_first_dir=None,
        )
        decision["path_ablation_runner_status"] = diagnostic_status
        diagnostic_summary_path = diagnostic_output / "suite_metrics.json"
        decision["path_ablation_summary"] = _summary_ref(diagnostic_summary_path)
        if diagnostic_status != 0 or not diagnostic_summary_path.exists():
            decision["decision"] = "PATH_ABLATION_IMPLEMENTATION_FAILURE"
            _write_json(decision_path, decision)
            return 1, decision
        evaluation = _diagnostic_evaluation(
            _load_json(diagnostic_summary_path),
            expected_mode=ablation.path_mode,
        )
        _write_json(diagnostic_output / "diagnostic_evaluation.json", evaluation)
        decision["path_ablation_evaluation"] = evaluation
        supported = bool(evaluation["new_base_rebuild_supported"])
        decision["path_ablation_new_base_rebuild_supported"] = supported
        if not bool(evaluation["evidence_contract_passed"]):
            decision["decision"] = "PATH_ABLATION_EVIDENCE_CONTRACT_FAILURE"
            _write_json(decision_path, decision)
            return 1, decision
        decision["decision"] = (
            "PATH_ABLATION_SUPPORTS_NEW_SINGLE_PATH_BASE_REBUILD"
            if supported
            else "PATH_ABLATION_FAILED_DISCARD_INTRINSIC_REPRICING_V1"
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
        / "run_aggtrade_intrinsic_repricing_nautilus.py",
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
