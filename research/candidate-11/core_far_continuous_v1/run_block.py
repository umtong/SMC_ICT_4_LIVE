#!/usr/bin/env python3
"""Execute one precommitted continuous development block."""
from __future__ import annotations

import argparse
from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
SOURCE_ROOT = HERE.parent / "session_portfolio_v1"
for path in (HERE, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evidence_audit import audit as audit_evidence  # noqa: E402
from runner import run  # noqa: E402


LOCKED_FILES = (
    "bar_adapter.py",
    "global_allocator.py",
    "logic.py",
    "market_leadership.py",
    "session_engine.py",
    "run_leadership_scdam_base.py",
    "runner_materializer.py",
    "semantic_execution.py",
    "semantic_logic.py",
    "semantic_market_leadership.py",
    "base_config.json",
    "evidence_audit.py",
)


def load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_protocol(protocol: dict[str, Any]) -> None:
    if protocol.get("research_stage") != "DEVELOPMENT":
        raise ValueError("this runner only accepts DEVELOPMENT protocols")
    if protocol.get("validation_eligible") is not False:
        raise ValueError("development blocks cannot be validation eligible")
    if protocol.get("success_claim_allowed") is not False:
        raise ValueError("development blocks cannot permit a success claim")
    scenario = protocol["scenario_contract"]
    if scenario.get("allowed_module") != "SCDAM_CORE":
        raise ValueError("only the SCDAM_CORE domain is authorized")
    if scenario.get("allowed_market_scenario") != "FAR":
        raise ValueError("only FAR is authorized")
    if scenario.get("new_fitted_thresholds"):
        raise ValueError("return-fitted threshold additions are forbidden")
    selection = protocol["selection"]
    if selection.get("data_use") != "OPENED_DEVELOPMENT_BY_DESIGN":
        raise ValueError("selected blocks must be development-only by design")
    if int(selection["resolution_tail_days"]) * 1440 <= int(
        scenario["time_invalidation"]["maximum_holding_minutes"]
    ):
        raise ValueError("resolution tail must exceed the maximum holding period")
    hierarchy = protocol["importance_contract"]["evidence_hierarchy"]
    if hierarchy["TEMPORARY_TEST"]["can_advance_candidate"] is not False:
        raise ValueError("temporary tests cannot advance a candidate")
    if hierarchy["DEVELOPMENT_GATE"]["can_claim_alpha"] is not False:
        raise ValueError("development evidence cannot claim alpha")
    if hierarchy["FRESH_VALIDATION"]["can_change_source_between_blocks"] is not False:
        raise ValueError("fresh-validation source must remain frozen")


def git_blob_sha(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return sha1(header + payload, usedforsecurity=False).hexdigest()


def source_lock(protocol: dict[str, Any]) -> dict[str, Any]:
    expected = protocol["locked_source"]["blobs"]
    records: dict[str, Any] = {}
    for name in LOCKED_FILES:
        path = SOURCE_ROOT / name
        payload = path.read_bytes()
        actual_blob = git_blob_sha(payload)
        if actual_blob != expected[name]:
            raise RuntimeError(
                f"locked parent source drifted for {name}: "
                f"expected {expected[name]}, found {actual_blob}"
            )
        records[name] = {
            "bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "origin_git_blob": expected[name],
            "origin_git_blob_verified": True,
        }
    for name in ("continuous_far_materializer.py", "runner.py", "run_block.py", "aggregate.py"):
        path = HERE / name
        if path.is_file():
            payload = path.read_bytes()
            records[name] = {
                "bytes": len(payload),
                "sha256": sha256(payload).hexdigest(),
                "origin_git_blob": None,
            }
    return {
        "schema": "candidate-11-core-far-source-lock-v1",
        "candidate": protocol["candidate"],
        "research_stage": protocol["research_stage"],
        "origin_branch": protocol["locked_source"]["origin_branch"],
        "files": records,
    }


def execute(block: str, output_dir: Path) -> dict[str, Any]:
    protocol = load_object(HERE / "protocol.json")
    validate_protocol(protocol)
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
        "research_stage": protocol["research_stage"],
        "validation_eligible": False,
        "scenario_domain": "SCDAM_CORE_FAR_TRANSFER",
        "maximum_holding_minutes": protocol["scenario_contract"][
            "time_invalidation"
        ]["maximum_holding_minutes"],
        "resolution_tail_days": protocol["selection"]["resolution_tail_days"],
        "block": block,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    effective_config = output_dir / "effective_config.json"
    write_json(effective_config, base)
    write_json(output_dir / "source_lock.json", source_lock(protocol))

    metrics = run(effective_config, block, output_dir)
    metrics_path = output_dir / "metrics.json"
    if metrics_path.is_file():
        metrics = load_object(metrics_path)
    metrics.update(
        {
            "candidate": protocol["candidate"],
            "protocol": protocol["schema"],
            "research_stage": "DEVELOPMENT",
            "validation_eligible": False,
            "individual_success_claim": False,
            "success_claim": False,
            "block": block,
            "block_role": blocks[block]["role"],
            "scenario_domain": "SCDAM_CORE_FAR_TRANSFER",
            "temporary_test": False,
            "evidence_use": [
                "causal scenario diagnosis",
                "continuous development-gate evidence",
            ],
        }
    )
    write_json(metrics_path, metrics)

    evidence = audit_evidence(output_dir, block)
    evidence.update(
        {
            "schema": "candidate-11-core-far-evidence-audit-v1",
            "candidate": protocol["candidate"],
            "research_stage": "DEVELOPMENT",
            "validation_eligible": False,
            "success_claim": False,
        }
    )
    write_json(output_dir / "audit.json", evidence)

    summary = {
        "candidate": protocol["candidate"],
        "protocol": protocol["schema"],
        "research_stage": "DEVELOPMENT",
        "validation_eligible": False,
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
        "scenario_max_hold_exit_count": metrics.get(
            "scenario_max_hold_exit_count", 0
        ),
        "resolution_tail_unresolved_count": metrics.get(
            "resolution_tail_unresolved_count", 0
        ),
        "safety_audit_passed": all(
            evidence.get(key) is True
            for key in (
                "evidence_complete",
                "metric_recalculation_passed",
                "risk_budget_passed",
                "global_slot_passed",
                "partial_entry_protection_passed",
                "no_liquidation_passed",
                "engine_errors_absent",
            )
        ),
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
