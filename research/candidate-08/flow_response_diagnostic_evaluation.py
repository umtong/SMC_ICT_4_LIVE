"""Evaluate a flow-response single-family diagnostic without promoting it.

A diagnostic can support rebuilding a new single-family base only when its evidence contract is
complete and every underlying economic/causal gate passes after excluding the two expected
both-family gates. It can never directly satisfy the project promotion gate.
"""

from __future__ import annotations

from typing import Any, Mapping

from aggtrade_flow_response_auction_signals_v3 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION


MODE_TO_RETAINED = {
    "initiative_only": INITIATIVE_FAMILY,
    "absorption_only": ABSORPTION_FAMILY,
}
_EXPECTED_FALSE_CHECKS = {
    "base_contract_includes_both_auction_families",
    "base_contract_includes_both_flow_response_families",
}
_REQUIRED_TRUE_EVIDENCE_CHECKS = {
    "complete_auction_scenario_attribution",
    "complete_post_run_trade_path_diagnostics",
}
_EVIDENCE_ONLY_CHECKS = _EXPECTED_FALSE_CHECKS | _REQUIRED_TRUE_EVIDENCE_CHECKS


def evaluate_diagnostic_summary(
    summary: Mapping[str, Any],
    *,
    expected_mode: str,
) -> dict[str, Any]:
    if expected_mode not in MODE_TO_RETAINED:
        raise ValueError(f"invalid diagnostic family mode: {expected_mode!r}")

    retained = MODE_TO_RETAINED[expected_mode]
    removed = ABSORPTION_FAMILY if retained == INITIATIVE_FAMILY else INITIATIVE_FAMILY
    raw_checks = summary.get("suite_gate_checks", {})
    checks_are_mapping = isinstance(raw_checks, Mapping)
    checks = dict(raw_checks) if checks_are_mapping else {}
    economic_checks = {
        key: bool(value)
        for key, value in checks.items()
        if key not in _EVIDENCE_ONLY_CHECKS
    }
    families = summary.get("scenario_family_results", {})
    retained_stats = dict(families.get(retained, {})) if isinstance(families, Mapping) else {}
    removed_stats = dict(families.get(removed, {})) if isinstance(families, Mapping) else {}
    reported_mode = str(
        summary.get(
            "flow_response_family_mode",
            summary.get("auction_family_mode", ""),
        )
    )
    path_summary = summary.get("trade_path_diagnostic_summary", {})
    path_summary_is_mapping = isinstance(path_summary, Mapping)
    closed_trades = int(summary.get("closed_trades", 0))
    expected_revision_counts = {DIAGNOSTIC_REVISION: closed_trades}

    evidence_checks = {
        "implementation_revision_exact": (
            str(summary.get("implementation_revision")) == IMPLEMENTATION_REVISION
        ),
        "ten_second_cadence_exact": (
            str(summary.get("ten_second_cadence_contract"))
            == "EXACT_CONSECUTIVE_10_SECONDS"
        ),
        "path_diagnostic_revision_exact": (
            str(summary.get("trade_path_diagnostic_revision")) == DIAGNOSTIC_REVISION
        ),
        "family_mode_exact": reported_mode == expected_mode,
        "diagnostic_flag_true": bool(summary.get("diagnostic_family_ablation", False)),
        "not_promotable": not bool(summary.get("promotable", True)),
        "suite_gate_remains_closed": not bool(summary.get("suite_gate_passed", True)),
        "scenario_attribution_complete": bool(
            summary.get("scenario_attribution_passed", False)
        ),
        "suite_gate_checks_are_mapping": checks_are_mapping,
        "both_family_gates_are_the_only_expected_exclusions": all(
            checks.get(name) is False for name in _EXPECTED_FALSE_CHECKS
        ),
        "required_evidence_checks_true": all(
            checks.get(name) is True for name in _REQUIRED_TRUE_EVIDENCE_CHECKS
        ),
        "trade_path_summary_present": path_summary_is_mapping,
        "trade_path_record_count_exact": (
            path_summary_is_mapping
            and int(path_summary.get("records", -1)) == closed_trades
        ),
        "trade_path_complete_count_exact": (
            path_summary_is_mapping
            and int(path_summary.get("complete_records", -1)) == closed_trades
        ),
        "trade_path_revision_counts_exact": (
            path_summary_is_mapping
            and dict(path_summary.get("diagnostic_revision_counts", {}))
            == expected_revision_counts
        ),
        "trade_path_expected_revision_exact": (
            path_summary_is_mapping
            and str(path_summary.get("expected_diagnostic_revision"))
            == DIAGNOSTIC_REVISION
        ),
        "economic_check_set_nonempty": len(economic_checks) >= 5,
        "removed_family_has_no_signals": int(removed_stats.get("signals", 0)) == 0,
        "removed_family_has_no_closed_trades": int(
            removed_stats.get("closed_trades", 0)
        ) == 0,
        "retained_family_name_present": retained in families,
    }
    evidence_contract_passed = all(evidence_checks.values())
    economic_checks_passed = bool(economic_checks) and all(economic_checks.values())
    return {
        "implementation_revision": IMPLEMENTATION_REVISION,
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
        "expected_mode": expected_mode,
        "retained_family": retained,
        "removed_family": removed,
        "evidence_contract_checks": evidence_checks,
        "evidence_contract_passed": evidence_contract_passed,
        "economic_checks": economic_checks,
        "economic_checks_passed": economic_checks_passed,
        "retained_family_results": retained_stats,
        "removed_family_results": removed_stats,
        "new_base_rebuild_supported": (
            evidence_contract_passed and economic_checks_passed
        ),
        "promotion_permitted": False,
    }


__all__ = [
    "MODE_TO_RETAINED",
    "evaluate_diagnostic_summary",
]
