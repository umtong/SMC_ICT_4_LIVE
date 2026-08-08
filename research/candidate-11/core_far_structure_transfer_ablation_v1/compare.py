#!/usr/bin/env python3
"""Compare the controlled structural risk-transfer ablation with its baseline."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from math import exp
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CORE_ROOT = HERE.parent / "core_far_continuous_v1"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from aggregate import aggregate as aggregate_account  # noqa: E402
from aggregate import load_object, write_json  # noqa: E402


def compare(results_root: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    baseline = load_object(CORE_ROOT / "aggregate.json")
    core_protocol = load_object(CORE_ROOT / "protocol.json")
    evaluation_protocol = deepcopy(core_protocol)
    evaluation_protocol["candidate"] = protocol["candidate"]
    ablation = aggregate_account(results_root, evaluation_protocol)

    baseline_by_id = {
        str(record["scenario_id"]): record for record in baseline["trades"]
    }
    ablation_by_id = {
        str(record["scenario_id"]): record for record in ablation["trades"]
    }
    expected_ids = set(protocol["baseline"]["scenario_ids"])
    baseline_ids = set(baseline_by_id)
    ablation_ids = set(ablation_by_id)
    exact_scenario_set = (
        baseline_ids == expected_ids
        and ablation_ids == expected_ids
        and len(ablation["trades"]) == len(expected_ids)
    )

    paired: list[dict[str, Any]] = []
    for scenario_id in sorted(expected_ids):
        baseline_trade = baseline_by_id.get(scenario_id)
        ablation_trade = ablation_by_id.get(scenario_id)
        paired.append(
            {
                "scenario_id": scenario_id,
                "baseline_log_growth": (
                    None
                    if baseline_trade is None
                    else float(baseline_trade["log_growth"])
                ),
                "ablation_log_growth": (
                    None
                    if ablation_trade is None
                    else float(ablation_trade["log_growth"])
                ),
                "paired_delta_log_growth": (
                    None
                    if baseline_trade is None or ablation_trade is None
                    else float(ablation_trade["log_growth"])
                    - float(baseline_trade["log_growth"])
                ),
                "baseline_direction": (
                    None if baseline_trade is None else baseline_trade["direction"]
                ),
                "ablation_direction": (
                    None if ablation_trade is None else ablation_trade["direction"]
                ),
            }
        )

    transfer_totals = {
        "requested": 0,
        "confirmed": 0,
        "not_improving": 0,
        "not_executable": 0,
        "modify_rejected": 0,
        "stop_lookup_failure": 0,
    }
    lifecycle_types: dict[str, int] = {}
    for block in protocol["selection"]["blocks"]:
        root = results_root / block
        metrics = load_object(root / "metrics.json")
        transfer_totals["requested"] += int(
            metrics.get("structural_risk_transfer_request_count", 0)
        )
        transfer_totals["confirmed"] += int(
            metrics.get("structural_risk_transfer_confirmed_count", 0)
        )
        transfer_totals["not_improving"] += int(
            metrics.get("structural_risk_transfer_not_improving_count", 0)
        )
        transfer_totals["not_executable"] += int(
            metrics.get("structural_risk_transfer_not_executable_count", 0)
        )
        lifecycle_path = root / "order_lifecycle.json"
        if lifecycle_path.is_file():
            lifecycle = load_object(lifecycle_path).get("events", [])
            for event in lifecycle if isinstance(lifecycle, list) else []:
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type", "UNKNOWN"))
                lifecycle_types[event_type] = lifecycle_types.get(event_type, 0) + 1
                if event_type == "ORDER_MODIFY_REJECTED":
                    transfer_totals["modify_rejected"] += 1
        audit = load_object(root / "audit.json")
        for error in audit.get("engine_errors", []) if isinstance(audit.get("engine_errors"), list) else []:
            if isinstance(error, dict) and error.get("type") == "STRUCTURAL_RISK_TRANSFER_STOP_LOOKUP_FAILURE":
                transfer_totals["stop_lookup_failure"] += 1

    baseline_blocks = {
        str(record["block"]): record for record in baseline["blocks"]
    }
    ablation_blocks = {
        str(record["block"]): record for record in ablation["blocks"]
    }
    block_comparison: list[dict[str, Any]] = []
    for block in protocol["selection"]["blocks"]:
        baseline_block = baseline_blocks[block]
        ablation_block = ablation_blocks.get(block)
        block_comparison.append(
            {
                "block": block,
                "baseline_log_growth": float(baseline_block["log_growth"]),
                "ablation_log_growth": (
                    None
                    if ablation_block is None
                    else float(ablation_block["log_growth"])
                ),
                "delta_log_growth": (
                    None
                    if ablation_block is None
                    else float(ablation_block["log_growth"])
                    - float(baseline_block["log_growth"])
                ),
                "baseline_nav_ratio": float(baseline_block["nav_ratio"]),
                "ablation_nav_ratio": (
                    None
                    if ablation_block is None
                    else float(ablation_block["nav_ratio"])
                ),
                "ablation_closed_trades": (
                    None
                    if ablation_block is None
                    else int(ablation_block["closed_trades"])
                ),
                "ablation_safety_audit_passed": (
                    False
                    if ablation_block is None
                    else bool(ablation_block["safety_audit_passed"])
                ),
            }
        )

    paired_improvements = [
        record
        for record in paired
        if record["paired_delta_log_growth"] is not None
        and float(record["paired_delta_log_growth"]) > 0.0
    ]
    positive_blocks = sum(
        record["ablation_log_growth"] is not None
        and float(record["ablation_log_growth"]) > 0.0
        for record in block_comparison
    )
    implementation_checks = {
        "all_blocks_complete": bool(ablation["checks"]["all_blocks_complete"]),
        "all_safety_audits": bool(ablation["checks"]["all_safety_audits"]),
        "no_resolution_tail_forced_exit": bool(
            ablation["checks"]["no_resolution_tail_forced_exit"]
        ),
        "trade_mapping_and_nav_reconciliation": bool(
            ablation["checks"]["trade_mapping_and_nav_reconciliation"]
        ),
        "no_modify_rejection": transfer_totals["modify_rejected"] == 0,
        "unique_stop_lookup": transfer_totals["stop_lookup_failure"] == 0,
    }
    controlled_checks = {
        "exact_scenario_id_set": exact_scenario_set,
        "same_trade_count": int(ablation["closed_trades"])
        == int(protocol["baseline"]["closed_trades"]),
        "same_direction_per_scenario": all(
            record["baseline_direction"] == record["ablation_direction"]
            for record in paired
            if record["baseline_direction"] is not None
            and record["ablation_direction"] is not None
        ),
    }
    improvement_checks = {
        "pooled_log_growth_improved": float(ablation["pooled_log_growth"])
        > float(baseline["pooled_log_growth"]),
        "pooled_log_growth_positive": float(ablation["pooled_log_growth"]) > 0.0,
        "at_least_two_positive_blocks": positive_blocks >= 2,
        "at_least_three_confirmed_transfers": transfer_totals["confirmed"] >= 3,
        "at_least_three_paired_scenarios_improved": len(paired_improvements) >= 3,
        "positive_leave_one_scenario_out_growth": float(
            ablation["minimum_leave_one_cluster_out_log_growth"]
        ) > 0.0,
        "positive_growth_not_concentrated": float(
            ablation["maximum_positive_log_growth_share_from_one_cluster"]
        ) <= 0.60,
    }

    if not all(implementation_checks.values()):
        classification = "STRUCTURAL_RISK_TRANSFER_IMPLEMENTATION_OR_EVIDENCE_FAILURE"
        decision = protocol["decision"]["implementation_failure"]
        mechanism_retained = False
    elif not all(controlled_checks.values()):
        classification = "STRUCTURAL_RISK_TRANSFER_UNCONTROLLED_COMPARISON"
        decision = protocol["decision"]["implementation_failure"]
        mechanism_retained = False
    elif not improvement_checks["pooled_log_growth_improved"]:
        classification = "STRUCTURAL_RISK_TRANSFER_REJECT"
        decision = protocol["decision"]["controlled_failure"]
        mechanism_retained = False
    elif all(improvement_checks.values()):
        classification = "STRUCTURAL_RISK_TRANSFER_RETAIN_DIAGNOSTIC_ONLY"
        decision = protocol["decision"]["retain_diagnostic_only"]
        mechanism_retained = True
    else:
        classification = "STRUCTURAL_RISK_TRANSFER_IMPROVED_BUT_INSUFFICIENT"
        decision = protocol["decision"]["improved_but_insufficient"]
        mechanism_retained = False

    return {
        "schema": "candidate-11-core-far-structure-transfer-comparison-v1",
        "candidate": protocol["candidate"],
        "research_stage": "TEMPORARY_TEST",
        "validation_eligible": False,
        "can_advance_candidate": False,
        "can_claim_alpha": False,
        "success_claim": False,
        "fresh_validation_authorized": False,
        "classification": classification,
        "mechanism_retained_for_new_development_candidate": mechanism_retained,
        "decision": decision,
        "implementation_checks": implementation_checks,
        "controlled_checks": controlled_checks,
        "improvement_checks": improvement_checks,
        "baseline": {
            "closed_trades": int(baseline["closed_trades"]),
            "pooled_log_growth": float(baseline["pooled_log_growth"]),
            "pooled_nav_multiple": float(baseline["pooled_nav_multiple"]),
            "pooled_daily_geometric_growth": float(
                baseline["pooled_daily_geometric_growth"]
            ),
        },
        "ablation": {
            "closed_trades": int(ablation["closed_trades"]),
            "pooled_log_growth": float(ablation["pooled_log_growth"]),
            "pooled_nav_multiple": float(ablation["pooled_nav_multiple"]),
            "pooled_daily_geometric_growth": float(
                ablation["pooled_daily_geometric_growth"]
            ),
            "minimum_leave_one_scenario_out_log_growth": float(
                ablation["minimum_leave_one_cluster_out_log_growth"]
            ),
            "maximum_positive_log_growth_share_from_one_scenario": float(
                ablation["maximum_positive_log_growth_share_from_one_cluster"]
            ),
            "positive_blocks": positive_blocks,
        },
        "delta": {
            "pooled_log_growth": float(ablation["pooled_log_growth"])
            - float(baseline["pooled_log_growth"]),
            "equivalent_nav_multiple": exp(
                float(ablation["pooled_log_growth"])
                - float(baseline["pooled_log_growth"])
            ),
            "paired_scenarios_improved": len(paired_improvements),
        },
        "transfer_totals": transfer_totals,
        "lifecycle_type_counts": lifecycle_types,
        "block_comparison": block_comparison,
        "paired_scenarios": paired,
        "ablation_account_evidence": ablation,
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Candidate 11 core FAR structural risk-transfer ablation",
        "",
        f"**{result['classification']}**",
        "",
        "This is an opened-data mechanism ablation. It cannot advance a candidate or claim alpha.",
        "",
        "## Account comparison",
        "",
        f"- baseline NAV multiple: `{result['baseline']['pooled_nav_multiple']:.10f}`",
        f"- ablation NAV multiple: `{result['ablation']['pooled_nav_multiple']:.10f}`",
        f"- baseline daily geometric growth: `{result['baseline']['pooled_daily_geometric_growth']:.10%}`",
        f"- ablation daily geometric growth: `{result['ablation']['pooled_daily_geometric_growth']:.10%}`",
        f"- pooled log-growth delta: `{result['delta']['pooled_log_growth']:.10f}`",
        f"- paired scenarios improved: `{result['delta']['paired_scenarios_improved']}`",
        f"- transfer requested / confirmed: `{result['transfer_totals']['requested']} / {result['transfer_totals']['confirmed']}`",
        "",
        "## Block comparison",
        "",
    ]
    for record in result["block_comparison"]:
        lines.append(
            f"- {record['block']}: baseline_log={record['baseline_log_growth']:.10f}, "
            f"ablation_log={record['ablation_log_growth']}, delta={record['delta_log_growth']}"
        )
    lines.extend(("", "## Checks", ""))
    for group in ("implementation_checks", "controlled_checks", "improvement_checks"):
        lines.append(f"### {group}")
        lines.extend(
            f"- {name}: `{passed}`"
            for name, passed in result[group].items()
        )
        lines.append("")
    lines.extend(("## Decision", "", result["decision"], ""))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.results, load_object(args.protocol))
    write_json(args.output, result)
    args.output.with_name("RESULT.md").write_text(
        render_markdown(result), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if "IMPLEMENTATION" not in result["classification"] and "UNCONTROLLED" not in result["classification"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
