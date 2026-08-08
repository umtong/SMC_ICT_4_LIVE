#!/usr/bin/env python3
"""Execute one controlled first-delivery/runner mechanism-ablation block."""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CORE_ROOT = HERE.parent / "core_far_continuous_v1"
SOURCE_ROOT = HERE.parent / "session_portfolio_v1"
for path in (HERE, CORE_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evidence_audit import audit as audit_evidence  # noqa: E402
from runner import run  # noqa: E402

_CORE_SPEC = importlib.util.spec_from_file_location(
    "candidate11_core_far_run_block_for_first_delivery",
    CORE_ROOT / "run_block.py",
)
if _CORE_SPEC is None or _CORE_SPEC.loader is None:
    raise ImportError("cannot load core FAR run_block module")
_CORE_RUN_BLOCK = importlib.util.module_from_spec(_CORE_SPEC)
_CORE_SPEC.loader.exec_module(_CORE_RUN_BLOCK)
load_object = _CORE_RUN_BLOCK.load_object
write_json = _CORE_RUN_BLOCK.write_json
core_source_lock = _CORE_RUN_BLOCK.source_lock
validate_core_protocol = _CORE_RUN_BLOCK.validate_protocol


POLICY = "SELF_FINANCING_FIRST_DELIVERY_EXTERNAL_RUNNER"
SINGLE_VARIABLE = "first_delivery_realization_topology"


def validate_protocol(
    protocol: dict[str, Any],
    core_protocol: dict[str, Any],
) -> None:
    if protocol.get("research_stage") != "TEMPORARY_TEST":
        raise ValueError("first-delivery ablation must remain TEMPORARY_TEST")
    for key in (
        "validation_eligible",
        "can_advance_candidate",
        "can_claim_alpha",
        "success_claim_allowed",
    ):
        if protocol.get(key) is not False:
            raise ValueError(f"{key} must remain false")
    if protocol["single_changed_variable"]["name"] != SINGLE_VARIABLE:
        raise ValueError("only first-delivery realization topology may change")
    if protocol["mechanism_contract"]["policy"] != POLICY:
        raise ValueError("unexpected first-delivery policy")
    baseline_ids = protocol["baseline"]["scenario_ids"]
    if len(baseline_ids) != 9 or len(set(baseline_ids)) != 9:
        raise ValueError("the exact nine baseline scenario IDs must be frozen")
    if protocol["selection"]["blocks"] != core_protocol["selection"]["blocks"]:
        raise ValueError("ablation blocks must equal the baseline blocks")
    for key in ("warmup_days", "evaluation_days", "resolution_tail_days", "seed"):
        if protocol["selection"][key] != core_protocol["selection"][key]:
            raise ValueError(f"selection field drifted: {key}")
    validate_core_protocol(core_protocol)


def source_lock(
    protocol: dict[str, Any],
    core_protocol: dict[str, Any],
) -> dict[str, Any]:
    lock = core_source_lock(core_protocol)
    lock.update(
        {
            "schema": "candidate-11-core-far-first-delivery-source-lock-v1",
            "candidate": protocol["candidate"],
            "research_stage": "TEMPORARY_TEST",
            "validation_eligible": False,
            "baseline_source_commit": protocol["baseline"]["source_commit"],
        }
    )
    for name in (
        "first_delivery_logic.py",
        "first_delivery_materializer.py",
        "runner.py",
        "run_block.py",
        "compare.py",
        "protocol.json",
        "test_first_delivery_logic.py",
        "test_materializer.py",
        "test_protocol.py",
    ):
        path = HERE / name
        if not path.is_file():
            continue
        payload = path.read_bytes()
        lock["files"][name] = {
            "bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "origin_git_blob": None,
        }
    return lock


def execute(block: str, output_dir: Path) -> dict[str, Any]:
    protocol = load_object(HERE / "protocol.json")
    core_protocol = load_object(CORE_ROOT / "protocol.json")
    validate_protocol(protocol, core_protocol)
    blocks = protocol["selection"]["blocks"]
    if block not in blocks:
        raise ValueError(f"unknown block {block!r}; expected one of {sorted(blocks)}")

    base = load_object(SOURCE_ROOT / "base_config.json")
    base["candidate"] = protocol["candidate"]
    base["selection"]["seed"] = protocol["selection"]["seed"]
    base["selection"]["warmup_days"] = protocol["selection"]["warmup_days"]
    base["selection"]["evaluation_days"] = protocol["selection"]["evaluation_days"]
    base["selection"]["weeks"] = {
        name: {
            "start": record["start"],
            "end_exclusive": record["end_exclusive"],
        }
        for name, record in blocks.items()
    }
    base["development_contract"] = {
        "schema": protocol["schema"],
        "research_stage": "TEMPORARY_TEST",
        "validation_eligible": False,
        "scenario_domain": "SCDAM_CORE_FAR_TRANSFER",
        "maximum_holding_minutes": core_protocol["scenario_contract"][
            "time_invalidation"
        ]["maximum_holding_minutes"],
        "resolution_tail_days": protocol["selection"]["resolution_tail_days"],
        "baseline_scenario_ids": protocol["baseline"]["scenario_ids"],
        "single_changed_variable": SINGLE_VARIABLE,
        "realization_policy": POLICY,
        "block": block,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    effective_config = output_dir / "effective_config.json"
    write_json(effective_config, base)
    write_json(output_dir / "source_lock.json", source_lock(protocol, core_protocol))

    metrics = run(effective_config, block, output_dir)
    metrics_path = output_dir / "metrics.json"
    if metrics_path.is_file():
        metrics = load_object(metrics_path)
    metrics.update(
        {
            "candidate": protocol["candidate"],
            "protocol": protocol["schema"],
            "research_stage": "TEMPORARY_TEST",
            "validation_eligible": False,
            "can_advance_candidate": False,
            "can_claim_alpha": False,
            "individual_success_claim": False,
            "success_claim": False,
            "block": block,
            "scenario_domain": "SCDAM_CORE_FAR_TRANSFER",
            "single_changed_variable": SINGLE_VARIABLE,
            "realization_policy": POLICY,
            "baseline_scenario_count": len(protocol["baseline"]["scenario_ids"]),
        }
    )
    write_json(metrics_path, metrics)

    evidence = audit_evidence(output_dir, block)
    evidence.update(
        {
            "schema": "candidate-11-core-far-first-delivery-audit-v1",
            "candidate": protocol["candidate"],
            "research_stage": "TEMPORARY_TEST",
            "validation_eligible": False,
            "can_advance_candidate": False,
            "success_claim": False,
        }
    )
    write_json(output_dir / "audit.json", evidence)

    safety_keys = (
        "evidence_complete",
        "metric_recalculation_passed",
        "risk_budget_passed",
        "global_slot_passed",
        "partial_entry_protection_passed",
        "no_liquidation_passed",
        "engine_errors_absent",
    )
    summary = {
        "candidate": protocol["candidate"],
        "protocol": protocol["schema"],
        "research_stage": "TEMPORARY_TEST",
        "validation_eligible": False,
        "can_advance_candidate": False,
        "can_claim_alpha": False,
        "success_claim": False,
        "block": block,
        "start": blocks[block]["start"],
        "end_exclusive": blocks[block]["end_exclusive"],
        "daily_geometric_growth": metrics.get("daily_geometric_growth"),
        "closed_trades": metrics.get("closed_trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "final_nav": metrics.get("final_nav"),
        "first_delivery_split_activated_count": metrics.get(
            "first_delivery_split_activated_count", 0
        ),
        "first_delivery_baseline_fallback_count": metrics.get(
            "first_delivery_baseline_fallback_count", 0
        ),
        "first_delivery_targets_submitted_count": metrics.get(
            "first_delivery_targets_submitted_count", 0
        ),
        "first_delivery_fill_count": metrics.get("first_delivery_fill_count", 0),
        "external_runner_fill_count": metrics.get("external_runner_fill_count", 0),
        "first_delivery_stop_fill_count": metrics.get(
            "first_delivery_stop_fill_count", 0
        ),
        "first_delivery_fail_closed_count": metrics.get(
            "first_delivery_fail_closed_count", 0
        ),
        "scenario_max_hold_exit_count": metrics.get(
            "scenario_max_hold_exit_count", 0
        ),
        "resolution_tail_unresolved_count": metrics.get(
            "resolution_tail_unresolved_count", 0
        ),
        "safety_audit_passed": all(evidence.get(key) is True for key in safety_keys),
        "audit_classification": evidence.get("classification"),
    }
    write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if evidence.get("classification") == "IMPLEMENTATION_OR_EVIDENCE_FAILURE":
        raise SystemExit(2)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("block")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    execute(args.block, args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
