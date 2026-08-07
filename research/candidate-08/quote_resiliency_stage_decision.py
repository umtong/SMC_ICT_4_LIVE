"""Evidence-first staged decision for external-liquidity quote resiliency.

This module never runs a market simulation and never changes thresholds. It validates committed
NautilusTrader artifacts, separates implementation/evidence failures from economic failures, and
permits the single predeclared confirmation quote-OFI ablation only after a clean base failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from aggtrade_acceptance_risk_v2 import RISK_ACCOUNTING_REVISION
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
from quote_resiliency_data_v2 import DATA_REVISION
from quote_resiliency_features_v3 import IMPLEMENTATION_REVISION as FEATURE_REVISION
from quote_resiliency_signals import (
    CONTINUATION_FAMILY,
    REVERSAL_FAMILY,
    SIGNAL_REVISION,
)
from quote_resiliency_strategy import EXECUTION_ADAPTER_REVISION
from run_quote_resiliency_nautilus import (
    BASE_ABLATION,
    CONFIG_IMPLEMENTATION_REVISION,
    OFI_ABLATION,
    RUNNER_REVISION,
)


DECISION_REVISION = "QUOTE_RESILIENCY_EVIDENCE_FIRST_STAGE_DECISION_V1"
EXPECTED_FAMILIES = frozenset((REVERSAL_FAMILY, CONTINUATION_FAMILY))
EXPECTED_CADENCE = "EXACT_CONSECUTIVE_COMPLETED_10_SECONDS"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _signals(summary: Mapping[str, Any]) -> int:
    families = summary.get("scenario_family_results", {})
    if not isinstance(families, Mapping):
        return 0
    return sum(
        int(metrics.get("signals", 0))
        for metrics in families.values()
        if isinstance(metrics, Mapping)
    )


def _window_metrics(root: Path, summary: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []
    windows = summary.get("windows", [])
    if not isinstance(windows, list):
        return tuple()
    for window in windows:
        if not isinstance(window, Mapping) or not window.get("name"):
            continue
        path = root / str(window["name"]) / "metrics.json"
        if path.exists():
            metrics = _load_json(path)
            metrics["_evidence_path"] = str(path)
            metrics["_evidence_sha256"] = _sha256(path)
            results.append(metrics)
    return tuple(results)


def validate_summary(
    summary: Mapping[str, Any],
    *,
    root: Path,
    expected_suite: str,
    diagnostic: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    expected_ablation = OFI_ABLATION if diagnostic else BASE_ABLATION
    expected_quote_gate = not diagnostic

    exact_fields = {
        "suite": expected_suite,
        "implementation_revision": CONFIG_IMPLEMENTATION_REVISION,
        "runner_revision": RUNNER_REVISION,
        "signal_revision": SIGNAL_REVISION,
        "feature_revision": FEATURE_REVISION,
        "data_revision": DATA_REVISION,
        "execution_adapter_revision": EXECUTION_ADAPTER_REVISION,
        "risk_accounting_revision": RISK_ACCOUNTING_REVISION,
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
        "ten_second_cadence_contract": EXPECTED_CADENCE,
        "ablation": expected_ablation,
    }
    for field, expected in exact_fields.items():
        if summary.get(field) != expected:
            errors.append(f"{field.upper()}_NOT_EXACT")

    if bool(summary.get("diagnostic_only_ablation", False)) != diagnostic:
        errors.append("DIAGNOSTIC_FLAG_NOT_EXACT")
    if bool(summary.get("quote_ofi_confirmation_required", not expected_quote_gate)) != expected_quote_gate:
        errors.append("QUOTE_OFI_CONFIRMATION_CONTRACT_NOT_EXACT")
    if bool(summary.get("promotable", False)) != (not diagnostic):
        errors.append("PROMOTION_FLAG_NOT_EXACT")
    if diagnostic and bool(summary.get("suite_gate_passed", False)):
        errors.append("DIAGNOSTIC_SUITE_GATE_MUST_REMAIN_CLOSED")
    if not bool(summary.get("scenario_attribution_passed", False)):
        errors.append("SCENARIO_ATTRIBUTION_INCOMPLETE")

    families = summary.get("scenario_family_results", {})
    if not isinstance(families, Mapping):
        errors.append("SCENARIO_FAMILY_RESULTS_MISSING")
    else:
        missing = EXPECTED_FAMILIES - set(map(str, families))
        if missing:
            errors.append("QUOTE_RESILIENCY_FAMILIES_MISSING")

    checks = summary.get("suite_gate_checks", {})
    if not isinstance(checks, Mapping):
        errors.append("SUITE_GATE_CHECKS_MISSING")
        checks = {}
    for key in (
        "complete_auction_scenario_attribution",
        "complete_post_run_trade_path_diagnostics",
        "both_quote_resiliency_families_enabled",
    ):
        if checks.get(key) is not True:
            errors.append(f"EVIDENCE_{key.upper()}_FAILED")
    if diagnostic:
        if checks.get("base_quote_ofi_confirmation_contract") is not False:
            errors.append("DIAGNOSTIC_BASE_QUOTE_GATE_FLAG_NOT_FALSE")
    elif checks.get("base_quote_ofi_confirmation_contract") is not True:
        errors.append("BASE_QUOTE_GATE_FLAG_NOT_TRUE")

    closed = int(summary.get("closed_trades", 0))
    path = summary.get("trade_path_diagnostic_summary", {})
    if not isinstance(path, Mapping):
        errors.append("TRADE_PATH_SUMMARY_MISSING")
    else:
        if int(path.get("records", -1)) != closed:
            errors.append("TRADE_PATH_RECORD_COUNT_MISMATCH")
        if int(path.get("complete_records", -1)) != closed:
            errors.append("TRADE_PATH_COMPLETE_COUNT_MISMATCH")
        if dict(path.get("diagnostic_revision_counts", {})) != {
            DIAGNOSTIC_REVISION: closed
        }:
            errors.append("TRADE_PATH_REVISION_COUNT_MISMATCH")

    windows = _window_metrics(root, summary)
    declared_windows = summary.get("windows", [])
    declared_count = len(declared_windows) if isinstance(declared_windows, list) else -1
    if len(windows) != declared_count:
        errors.append("WINDOW_METRICS_COUNT_MISMATCH")

    window_evidence: list[dict[str, Any]] = []
    execution_risk_failures = 0
    blocking_contract_failures = 0
    required_true = (
        "all_signal_times_processed",
        "all_submitted_entries_observed",
        "closed_trades_matched_to_intents",
        "entry_causality",
        "funding_cost_state_is_causal_and_complete",
        "planned_risk_budget_respected",
        "position_exit_causality",
        "realized_loss_budget_respected",
        "no_residual_exposure",
        "no_unexpected_or_liquidation_closes",
    )
    for metrics in windows:
        window_checks = metrics.get("first_window_gate_checks", {})
        if not isinstance(window_checks, Mapping):
            window_checks = {}
            blocking_contract_failures += 1
        failed_required = [key for key in required_true if window_checks.get(key) is not True]
        classification = metrics.get("execution_contract_classification", {})
        risk_failure = bool(
            isinstance(classification, Mapping)
            and classification.get("candidate_gate_failure") is True
            and classification.get("implementation_failure") is False
        )
        execution_risk_failures += int(risk_failure)
        if failed_required:
            blocking_contract_failures += 1
        if int(metrics.get("open_positions_after_run", -1)) != 0:
            blocking_contract_failures += 1
        if int(metrics.get("open_orders_after_run", -1)) != 0:
            blocking_contract_failures += 1
        if int(metrics.get("unprocessed_signal_times", -1)) != 0:
            blocking_contract_failures += 1
        window_evidence.append(
            {
                "name": metrics.get("window", {}).get("name"),
                "path": metrics.get("_evidence_path"),
                "sha256": metrics.get("_evidence_sha256"),
                "failed_required_contracts": failed_required,
                "execution_risk_failure": risk_failure,
                "closed_trades": int(metrics.get("position_metrics", {}).get("closed_trades", 0)),
                "total_return": float(metrics.get("total_return", 0.0)),
            }
        )

    return {
        "passed": not errors,
        "errors": errors,
        "window_evidence": window_evidence,
        "execution_risk_failure_windows": execution_risk_failures,
        "blocking_contract_failure_windows": blocking_contract_failures,
    }


def classify_base(
    summary: Mapping[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    suite = str(summary.get("suite", ""))
    evidence = validate_summary(
        summary,
        root=root,
        expected_suite=suite,
        diagnostic=False,
    )
    if not evidence["passed"]:
        decision = "BASE_EVIDENCE_CONTRACT_FAILURE"
        run_ablation = False
    elif evidence["blocking_contract_failure_windows"]:
        decision = "BASE_IMPLEMENTATION_OR_EVIDENCE_FAILURE"
        run_ablation = False
    elif evidence["execution_risk_failure_windows"]:
        decision = "BASE_EXECUTION_RISK_FAILURE"
        run_ablation = False
    elif bool(summary.get("suite_gate_passed", False)):
        decision = (
            "ADVANCE_TO_FROZEN_SCREEN_WEEKS"
            if suite == "first"
            else "ADVANCE_TO_PREDECLARED_LONG_EVALUATION"
        )
        run_ablation = False
    else:
        signals = _signals(summary)
        closed = int(summary.get("closed_trades", 0))
        total_return_positive = float(summary.get("combined_daily_geometric_growth", 0.0)) > 0.0
        if signals == 0 or closed == 0:
            decision = "CLEAN_LOGIC_OPPORTUNITY_FAILURE_RUN_SINGLE_ABLATION"
        elif not total_return_positive:
            decision = "CLEAN_LOGIC_ECONOMIC_FAILURE_RUN_SINGLE_ABLATION"
        else:
            decision = "CLEAN_GATE_SHORTFALL_RUN_SINGLE_ABLATION"
        run_ablation = True
    return {
        "decision": decision,
        "single_ablation_permitted": run_ablation,
        "evidence": evidence,
        "signals": _signals(summary),
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


def classify_ablation(
    summary: Mapping[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    suite = str(summary.get("suite", ""))
    evidence = validate_summary(
        summary,
        root=root,
        expected_suite=suite,
        diagnostic=True,
    )
    raw_checks = summary.get("suite_gate_checks", {})
    checks = dict(raw_checks) if isinstance(raw_checks, Mapping) else {}
    excluded = {
        "base_contract_includes_both_auction_families",
        "base_contract_includes_both_flow_response_families",
        "base_quote_ofi_confirmation_contract",
    }
    economic_checks = {
        key: bool(value) for key, value in checks.items() if key not in excluded
    }
    economic_passed = bool(economic_checks) and all(economic_checks.values())
    if not evidence["passed"] or evidence["blocking_contract_failure_windows"]:
        decision = "SINGLE_ABLATION_IMPLEMENTATION_OR_EVIDENCE_FAILURE"
    elif evidence["execution_risk_failure_windows"]:
        decision = "SINGLE_ABLATION_EXECUTION_RISK_FAILURE_DISCARD_V1"
    elif economic_passed:
        decision = "ABLATION_SUPPORTS_NEW_BASE_REBUILD_NOT_PROMOTION"
    else:
        decision = "SINGLE_ABLATION_FAILED_DISCARD_QUOTE_RESILIENCY_V1"
    return {
        "decision": decision,
        "promotion_permitted": False,
        "evidence": evidence,
        "economic_checks": economic_checks,
        "economic_checks_passed": economic_passed,
        "signals": _signals(summary),
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


def build_decision(
    *,
    base_root: Path,
    ablation_root: Path | None = None,
) -> dict[str, Any]:
    base_path = base_root / "suite_metrics.json"
    if not base_path.exists():
        return {
            "decision_revision": DECISION_REVISION,
            "decision": "BASE_SUITE_METRICS_MISSING",
            "base_root": str(base_root),
            "single_ablation_permitted": False,
        }
    base_summary = _load_json(base_path)
    base = classify_base(base_summary, root=base_root)
    result: dict[str, Any] = {
        "decision_revision": DECISION_REVISION,
        "base_root": str(base_root),
        "base_suite_metrics_sha256": _sha256(base_path),
        "base": base,
        "decision": base["decision"],
        "single_ablation_permitted": base["single_ablation_permitted"],
        "promotion_permitted_from_ablation": False,
    }
    if ablation_root is not None:
        ablation_path = ablation_root / "suite_metrics.json"
        if not base["single_ablation_permitted"]:
            result["ablation"] = {
                "decision": "ABLATION_NOT_PERMITTED_BY_BASE_CLASSIFICATION",
                "promotion_permitted": False,
            }
        elif not ablation_path.exists():
            result["decision"] = "SINGLE_ABLATION_SUITE_METRICS_MISSING"
            result["ablation"] = {
                "decision": result["decision"],
                "promotion_permitted": False,
            }
        else:
            ablation_summary = _load_json(ablation_path)
            ablation = classify_ablation(ablation_summary, root=ablation_root)
            result["ablation_root"] = str(ablation_root)
            result["ablation_suite_metrics_sha256"] = _sha256(ablation_path)
            result["ablation"] = ablation
            result["decision"] = ablation["decision"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-root", type=Path, required=True)
    parser.add_argument("--ablation-root", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    decision = build_decision(
        base_root=args.base_root.resolve(),
        ablation_root=(
            None if args.ablation_root is None else args.ablation_root.resolve()
        ),
    )
    _write_json(args.output.resolve(), decision)
    print(json.dumps(decision, indent=2, sort_keys=True), flush=True)
    return 1 if "IMPLEMENTATION" in str(decision["decision"]) or "EVIDENCE" in str(decision["decision"]) or "MISSING" in str(decision["decision"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
