#!/usr/bin/env python3
"""Compare the controlled first-delivery/runner ablation with core FAR."""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal, InvalidOperation
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


POLICY = "SELF_FINANCING_FIRST_DELIVERY_EXTERNAL_RUNNER"


def _plans(path: Path) -> list[dict[str, Any]]:
    payload = load_object(path)
    records = payload.get("plans")
    return [item for item in records if isinstance(item, dict)] if isinstance(records, list) else []


def _allocation_audit(results_root: Path, blocks: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    active = 0
    fallback = 0
    kinds: dict[str, int] = {}
    margins: list[float] = []
    for block in blocks:
        for plan in _plans(results_root / block / "submitted_plans.json"):
            details = plan.get("details") if isinstance(plan.get("details"), dict) else {}
            activation = str(details.get("first_delivery_activation", ""))
            if activation == "SPLIT_ACTIVE":
                active += 1
                try:
                    total = Decimal(str(plan["quantity"]))
                    primary = Decimal(str(details["first_delivery_primary_quantity"]))
                    runner = Decimal(str(details["external_runner_quantity"]))
                    margin = Decimal(str(details["rounded_self_financing_margin"]))
                    first_target = Decimal(str(details["first_delivery_target"]))
                    entry = Decimal(str(plan["entry"]))
                    final_target = Decimal(str(plan["target"]))
                    direction = str(plan["direction"])
                    if primary <= 0 or runner <= 0 or primary + runner != total:
                        errors.append(f"{plan['scenario_id']}: invalid split quantities")
                    if margin < 0:
                        errors.append(f"{plan['scenario_id']}: negative rounded self-financing margin")
                    if direction == "LONG" and not entry < first_target < final_target:
                        errors.append(f"{plan['scenario_id']}: non-causal long target order")
                    if direction == "SHORT" and not final_target < first_target < entry:
                        errors.append(f"{plan['scenario_id']}: non-causal short target order")
                    margins.append(float(margin))
                    kind = str(details.get("first_delivery_kind", "UNKNOWN"))
                    kinds[kind] = kinds.get(kind, 0) + 1
                except (KeyError, InvalidOperation, ValueError) as exc:
                    errors.append(f"{plan.get('scenario_id')}: split evidence error: {exc}")
            elif activation.startswith("BASELINE_FALLBACK"):
                fallback += 1
            else:
                errors.append(
                    f"{plan.get('scenario_id')}: missing explicit activation classification"
                )
    return {
        "passed": not errors,
        "errors": errors,
        "active_plans": active,
        "fallback_plans": fallback,
        "kind_counts": kinds,
        "minimum_rounded_self_financing_margin": min(margins) if margins else None,
    }


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
    exact_scenario_set = (
        set(baseline_by_id) == expected_ids
        and set(ablation_by_id) == expected_ids
        and len(ablation["trades"]) == len(expected_ids)
    )

    paired: list[dict[str, Any]] = []
    for scenario_id in sorted(expected_ids):
        base_trade = baseline_by_id.get(scenario_id)
        test_trade = ablation_by_id.get(scenario_id)
        paired.append(
            {
                "scenario_id": scenario_id,
                "baseline_log_growth": None if base_trade is None else float(base_trade["log_growth"]),
                "ablation_log_growth": None if test_trade is None else float(test_trade["log_growth"]),
                "paired_delta_log_growth": (
                    None
                    if base_trade is None or test_trade is None
                    else float(test_trade["log_growth"]) - float(base_trade["log_growth"])
                ),
                "baseline_direction": None if base_trade is None else base_trade["direction"],
                "ablation_direction": None if test_trade is None else test_trade["direction"],
            }
        )

    lifecycle_counts: dict[str, int] = {}
    metric_totals = {
        "split_activated": 0,
        "baseline_fallback": 0,
        "targets_submitted": 0,
        "first_delivery_fills": 0,
        "runner_fills": 0,
        "stop_fills": 0,
        "stop_resize_requests": 0,
        "fail_closed": 0,
    }
    baseline_blocks = {str(record["block"]): record for record in baseline["blocks"]}
    ablation_blocks = {str(record["block"]): record for record in ablation["blocks"]}
    block_comparison: list[dict[str, Any]] = []
    for block in protocol["selection"]["blocks"]:
        metrics = load_object(results_root / block / "metrics.json")
        metric_totals["split_activated"] += int(metrics.get("first_delivery_split_activated_count", 0))
        metric_totals["baseline_fallback"] += int(metrics.get("first_delivery_baseline_fallback_count", 0))
        metric_totals["targets_submitted"] += int(metrics.get("first_delivery_targets_submitted_count", 0))
        metric_totals["first_delivery_fills"] += int(metrics.get("first_delivery_fill_count", 0))
        metric_totals["runner_fills"] += int(metrics.get("external_runner_fill_count", 0))
        metric_totals["stop_fills"] += int(metrics.get("first_delivery_stop_fill_count", 0))
        metric_totals["stop_resize_requests"] += int(metrics.get("first_delivery_stop_resize_request_count", 0))
        metric_totals["fail_closed"] += int(metrics.get("first_delivery_fail_closed_count", 0))
        lifecycle = load_object(results_root / block / "order_lifecycle.json").get("events", [])
        for event in lifecycle if isinstance(lifecycle, list) else []:
            if isinstance(event, dict):
                kind = str(event.get("type", "UNKNOWN"))
                lifecycle_counts[kind] = lifecycle_counts.get(kind, 0) + 1

        base_block = baseline_blocks[block]
        test_block = ablation_blocks.get(block)
        block_comparison.append(
            {
                "block": block,
                "baseline_log_growth": float(base_block["log_growth"]),
                "ablation_log_growth": None if test_block is None else float(test_block["log_growth"]),
                "delta_log_growth": (
                    None
                    if test_block is None
                    else float(test_block["log_growth"]) - float(base_block["log_growth"])
                ),
                "baseline_nav_ratio": float(base_block["nav_ratio"]),
                "ablation_nav_ratio": None if test_block is None else float(test_block["nav_ratio"]),
                "ablation_closed_trades": None if test_block is None else int(test_block["closed_trades"]),
                "ablation_safety_audit_passed": False if test_block is None else bool(test_block["safety_audit_passed"]),
            }
        )

    allocation = _allocation_audit(results_root, protocol["selection"]["blocks"])
    paired_improvements = [
        item
        for item in paired
        if item["paired_delta_log_growth"] is not None
        and float(item["paired_delta_log_growth"]) > 0.0
    ]
    positive_blocks = sum(
        item["ablation_log_growth"] is not None
        and float(item["ablation_log_growth"]) > 0.0
        for item in block_comparison
    )
    implementation_checks = {
        "all_blocks_complete": bool(ablation["checks"]["all_blocks_complete"]),
        "all_safety_audits": bool(ablation["checks"]["all_safety_audits"]),
        "no_resolution_tail_forced_exit": bool(ablation["checks"]["no_resolution_tail_forced_exit"]),
        "trade_mapping_and_nav_reconciliation": bool(ablation["checks"]["trade_mapping_and_nav_reconciliation"]),
        "no_first_delivery_fail_close": metric_totals["fail_closed"] == 0,
        "allocation_contract": bool(allocation["passed"]),
        "all_activated_targets_submitted": metric_totals["targets_submitted"]
        == metric_totals["split_activated"],
    }
    controlled_checks = {
        "exact_scenario_id_set": exact_scenario_set,
        "same_trade_count": int(ablation["closed_trades"])
        == int(protocol["baseline"]["closed_trades"]),
        "same_direction_per_scenario": all(
            item["baseline_direction"] == item["ablation_direction"]
            for item in paired
            if item["baseline_direction"] is not None
            and item["ablation_direction"] is not None
        ),
    }
    economic_checks = {
        "pooled_log_growth_improved": float(ablation["pooled_log_growth"])
        > float(baseline["pooled_log_growth"]),
        "pooled_log_growth_positive": float(ablation["pooled_log_growth"]) > 0.0,
        "at_least_two_positive_blocks": positive_blocks >= 2,
        "at_least_three_first_delivery_fills": metric_totals["first_delivery_fills"] >= 3,
        "at_least_one_external_runner_fill": metric_totals["runner_fills"] >= 1,
        "at_least_three_paired_scenarios_improved": len(paired_improvements) >= 3,
        "positive_leave_one_scenario_out_growth": float(
            ablation["minimum_leave_one_cluster_out_log_growth"]
        ) > 0.0,
        "positive_growth_not_concentrated": float(
            ablation["maximum_positive_log_growth_share_from_one_cluster"]
        ) <= 0.60,
    }

    if not all(implementation_checks.values()):
        classification = "FIRST_DELIVERY_IMPLEMENTATION_OR_EVIDENCE_FAILURE"
        decision = protocol["decision"]["implementation_failure"]
        retained = False
    elif not all(controlled_checks.values()):
        classification = "FIRST_DELIVERY_UNCONTROLLED_COMPARISON"
        decision = protocol["decision"]["implementation_failure"]
        retained = False
    elif not economic_checks["pooled_log_growth_improved"]:
        classification = "FIRST_DELIVERY_REJECT"
        decision = protocol["decision"]["controlled_failure"]
        retained = False
    elif all(economic_checks.values()):
        classification = "FIRST_DELIVERY_RETAIN_DIAGNOSTIC_ONLY"
        decision = protocol["decision"]["retain_diagnostic_only"]
        retained = True
    else:
        classification = "FIRST_DELIVERY_IMPROVED_BUT_INSUFFICIENT"
        decision = protocol["decision"]["improved_but_insufficient"]
        retained = False

    return {
        "schema": "candidate-11-core-far-first-delivery-comparison-v1",
        "candidate": protocol["candidate"],
        "research_stage": "TEMPORARY_TEST",
        "validation_eligible": False,
        "can_advance_candidate": False,
        "can_claim_alpha": False,
        "success_claim": False,
        "fresh_validation_authorized": False,
        "classification": classification,
        "mechanism_retained_for_new_development_candidate": retained,
        "decision": decision,
        "implementation_checks": implementation_checks,
        "controlled_checks": controlled_checks,
        "economic_checks": economic_checks,
        "allocation_audit": allocation,
        "baseline": {
            "closed_trades": int(baseline["closed_trades"]),
            "pooled_log_growth": float(baseline["pooled_log_growth"]),
            "pooled_nav_multiple": float(baseline["pooled_nav_multiple"]),
            "pooled_daily_geometric_growth": float(baseline["pooled_daily_geometric_growth"]),
        },
        "ablation": {
            "closed_trades": int(ablation["closed_trades"]),
            "pooled_log_growth": float(ablation["pooled_log_growth"]),
            "pooled_nav_multiple": float(ablation["pooled_nav_multiple"]),
            "pooled_daily_geometric_growth": float(ablation["pooled_daily_geometric_growth"]),
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
        "mechanism_totals": metric_totals,
        "lifecycle_type_counts": lifecycle_counts,
        "block_comparison": block_comparison,
        "paired_scenarios": paired,
        "ablation_account_evidence": ablation,
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Candidate 11 core FAR first-delivery / external-runner ablation",
        "",
        f"**{result['classification']}**",
        "",
        "Opened-data TEMPORARY_TEST only; it cannot advance a candidate or claim alpha.",
        "",
        "## Account comparison",
        "",
        f"- baseline NAV multiple: `{result['baseline']['pooled_nav_multiple']:.10f}`",
        f"- ablation NAV multiple: `{result['ablation']['pooled_nav_multiple']:.10f}`",
        f"- baseline daily geometric growth: `{result['baseline']['pooled_daily_geometric_growth']:.10%}`",
        f"- ablation daily geometric growth: `{result['ablation']['pooled_daily_geometric_growth']:.10%}`",
        f"- pooled log-growth delta: `{result['delta']['pooled_log_growth']:.10f}`",
        f"- paired scenarios improved: `{result['delta']['paired_scenarios_improved']}`",
        "",
        "## Realization events",
        "",
    ]
    for key, value in result["mechanism_totals"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(("", "## Block comparison", ""))
    for record in result["block_comparison"]:
        lines.append(
            f"- {record['block']}: baseline_log={record['baseline_log_growth']:.10f}, "
            f"ablation_log={record['ablation_log_growth']}, delta={record['delta_log_growth']}"
        )
    lines.extend(("", "## Checks", ""))
    for group in ("implementation_checks", "controlled_checks", "economic_checks"):
        lines.append(f"### {group}")
        lines.extend(f"- {name}: `{passed}`" for name, passed in result[group].items())
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
    args.output.with_name("RESULT.md").write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if "IMPLEMENTATION" not in result["classification"] and "UNCONTROLLED" not in result["classification"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
