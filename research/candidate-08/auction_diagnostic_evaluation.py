"""Validate a candidate-08 single-family diagnostic without promoting it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from auction_family_ablation_decision import (
    FAILED_AUCTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)


MODE_TO_RETAINED = {
    "initiative_only": INITIATIVE_FAMILY,
    "failed_auction_only": FAILED_AUCTION_FAMILY,
}


def evaluate_diagnostic_summary(
    summary: Mapping[str, Any],
    *,
    expected_mode: str,
) -> dict[str, Any]:
    if expected_mode not in MODE_TO_RETAINED:
        raise ValueError(f"invalid diagnostic family mode: {expected_mode!r}")

    checks = dict(summary.get("suite_gate_checks", {}))
    excluded = {"base_contract_includes_both_auction_families"}
    economic_checks = {
        key: bool(value)
        for key, value in checks.items()
        if key not in excluded
    }
    families = summary.get("scenario_family_results", {})
    retained = MODE_TO_RETAINED[expected_mode]
    removed = (
        FAILED_AUCTION_FAMILY
        if retained == INITIATIVE_FAMILY
        else INITIATIVE_FAMILY
    )
    retained_stats = dict(families.get(retained, {})) if isinstance(families, Mapping) else {}
    removed_stats = dict(families.get(removed, {})) if isinstance(families, Mapping) else {}

    contract_checks = {
        "implementation_revision_exact": (
            str(summary.get("implementation_revision")) == IMPLEMENTATION_REVISION
        ),
        "family_mode_exact": str(summary.get("auction_family_mode")) == expected_mode,
        "diagnostic_flag_true": bool(summary.get("diagnostic_family_ablation", False)),
        "not_promotable": not bool(summary.get("promotable", True)),
        "suite_gate_remains_closed": not bool(summary.get("suite_gate_passed", True)),
        "scenario_attribution_complete": bool(
            summary.get("scenario_attribution_passed", False)
        ),
        "base_both_family_gate_is_only_expected_exclusion": (
            checks.get("base_contract_includes_both_auction_families") is False
        ),
        "economic_check_set_nonempty": len(economic_checks) >= 5,
        "removed_family_has_no_signals": int(removed_stats.get("signals", 0)) == 0,
        "removed_family_has_no_closed_trades": (
            int(removed_stats.get("closed_trades", 0)) == 0
        ),
        "retained_family_name_present": retained in families,
    }
    evidence_contract_passed = all(contract_checks.values())
    economic_checks_passed = bool(economic_checks) and all(economic_checks.values())
    return {
        "implementation_revision": IMPLEMENTATION_REVISION,
        "expected_mode": expected_mode,
        "retained_family": retained,
        "removed_family": removed,
        "evidence_contract_checks": contract_checks,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument(
        "--expected-mode",
        choices=tuple(sorted(MODE_TO_RETAINED)),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()

    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    evaluation = evaluate_diagnostic_summary(
        summary,
        expected_mode=args.expected_mode,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evaluation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with args.github_output.open("a", encoding="utf-8") as stream:
        stream.write(
            "evidence_contract_passed="
            f"{'true' if evaluation['evidence_contract_passed'] else 'false'}\n"
        )
        stream.write(
            "economic_checks_passed="
            f"{'true' if evaluation['economic_checks_passed'] else 'false'}\n"
        )
        stream.write(
            "new_base_rebuild_supported="
            f"{'true' if evaluation['new_base_rebuild_supported'] else 'false'}\n"
        )
    print(json.dumps(evaluation, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
