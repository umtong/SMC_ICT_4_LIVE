"""Evaluate a flow-response single-family diagnostic without promoting it.

A diagnostic can support rebuilding a new single-family base only when its evidence contract is
complete and every underlying economic/causal gate passes after excluding the two expected
both-family gates. It can never directly satisfy the project promotion gate.
"""

from __future__ import annotations

from typing import Any, Mapping

from aggtrade_flow_response_auction_signals_v2 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)


MODE_TO_RETAINED = {
    "initiative_only": INITIATIVE_FAMILY,
    "absorption_only": ABSORPTION_FAMILY,
}
_EXPECTED_EXCLUDED_CHECKS = {
    "base_contract_includes_both_auction_families",
    "base_contract_includes_both_flow_response_families",
}


def evaluate_diagnostic_summary(
    summary: Mapping[str, Any],
    *,
    expected_mode: str,
) -> dict[str, Any]:
    if expected_mode not in MODE_TO_RETAINED:
        raise ValueError(f"invalid diagnostic family mode: {expected_mode!r}")

    retained = MODE_TO_RETAINED[expected_mode]
    removed = ABSORPTION_FAMILY if retained == INITIATIVE_FAMILY else INITIATIVE_FAMILY
    checks = dict(summary.get("suite_gate_checks", {}))
    economic_checks = {
        key: bool(value)
        for key, value in checks.items()
        if key not in _EXPECTED_EXCLUDED_CHECKS
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

    evidence_checks = {
        "implementation_revision_exact": (
            str(summary.get("implementation_revision")) == IMPLEMENTATION_REVISION
        ),
        "family_mode_exact": reported_mode == expected_mode,
        "diagnostic_flag_true": bool(summary.get("diagnostic_family_ablation", False)),
        "not_promotable": not bool(summary.get("promotable", True)),
        "suite_gate_remains_closed": not bool(summary.get("suite_gate_passed", True)),
        "scenario_attribution_complete": bool(
            summary.get("scenario_attribution_passed", False)
        ),
        "both_family_gates_are_the_only_expected_exclusions": all(
            checks.get(name) is False for name in _EXPECTED_EXCLUDED_CHECKS
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
