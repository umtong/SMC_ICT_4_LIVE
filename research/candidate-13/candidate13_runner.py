#!/usr/bin/env python3
"""Frozen Candidate 13 holdout runner.

This wrapper does not alter the imported trading state machine. It binds an
explicit protocol calendar, verifies the protocol's Git-blob source lock before
any market archive is requested, invokes the NautilusTrader runner, then
annotates and independently audits the emitted evidence.
"""
from __future__ import annotations

import argparse
from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_audit import audit
from run_leadership_scdam import run


DEFAULT_LOCKED_FILES = (
    "bar_adapter.py",
    "global_allocator.py",
    "logic.py",
    "market_leadership.py",
    "session_engine.py",
    "run_leadership_scdam.py",
    "evidence_audit.py",
    "base_config.json",
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def git_blob_oid(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return sha1(header + payload).hexdigest()


def source_lock(protocol: dict[str, Any]) -> dict[str, Any]:
    locked = protocol["locked_source"]
    expected = locked["blobs"]
    if not isinstance(expected, dict) or not expected:
        raise ValueError("locked_source.blobs must be a non-empty object")

    enforce = bool(locked.get("enforce_git_blobs", False))
    actual: dict[str, Any] = {}
    mismatches: list[str] = []
    for name in sorted(expected):
        path = ROOT / name
        if not path.is_file():
            mismatches.append(f"{name}: missing")
            continue
        payload = path.read_bytes()
        oid = git_blob_oid(payload)
        expected_oid = str(expected[name])
        matched = oid == expected_oid
        if not matched:
            mismatches.append(f"{name}: expected {expected_oid}, actual {oid}")
        actual[name] = {
            "bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "git_blob": oid,
            "expected_git_blob": expected_oid,
            "matched": matched,
        }

    if enforce and mismatches:
        raise RuntimeError(
            "Candidate 13 frozen source mismatch before data access:\n"
            + "\n".join(mismatches),
        )

    return {
        "schema": "candidate-13-source-lock-v2",
        "candidate": protocol["candidate"],
        "origin_branch": locked["origin_branch"],
        "strategy_freeze_commit": locked.get("strategy_freeze_commit"),
        "development_evidence_commit": locked.get("development_evidence_commit"),
        "enforced": enforce,
        "all_matched": not mismatches,
        "mismatches": mismatches,
        "files": actual,
    }


def annotate(path: Path, updates: dict[str, Any]) -> None:
    if not path.is_file():
        return
    payload = load_object(path)
    payload.update(updates)
    write_json(path, payload)


def execute(
    week: str,
    output_dir: Path,
    *,
    protocol_path: Path | None = None,
) -> dict[str, Any]:
    selected_protocol = (protocol_path or (ROOT / "protocol.json")).resolve()
    protocol = load_object(selected_protocol)
    holdouts = protocol["selection"]["holdouts"]
    if week not in holdouts:
        raise ValueError(
            f"unknown frozen holdout {week!r}; expected one of {sorted(holdouts)}",
        )

    # Verify byte-exact trading source before writing a config or allowing the
    # inherited runner to download any market archive.
    verified_lock = source_lock(protocol)

    config = load_object(ROOT / "base_config.json")
    config["candidate"] = protocol["candidate"]
    config["selection"]["seed"] = protocol["selection"]["seed"]
    config["selection"]["warmup_days"] = protocol["selection"]["warmup_days"]
    config["selection"]["weeks"] = {
        name: {
            "start": record["start"],
            "end_exclusive": record["end_exclusive"],
        }
        for name, record in holdouts.items()
    }
    config["candidate13_protocol"] = {
        "schema": protocol["schema"],
        "protocol_file": selected_protocol.name,
        "holdout": week,
        "role": holdouts[week]["role"],
        "aggregate_gate": protocol["aggregate_gate"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    effective_config = output_dir / "effective_config.json"
    write_json(effective_config, config)
    write_json(output_dir / "source_lock.json", verified_lock)

    metrics = run(effective_config, week, output_dir)
    metrics_path = output_dir / "metrics.json"
    if metrics_path.is_file():
        metrics = load_object(metrics_path)
    metrics.update(
        {
            "candidate": protocol["candidate"],
            "candidate13_protocol": protocol["schema"],
            "candidate13_protocol_file": selected_protocol.name,
            "holdout_role": holdouts[week]["role"],
            "source_origin_branch": protocol["locked_source"]["origin_branch"],
            "strategy_freeze_commit": protocol["locked_source"].get(
                "strategy_freeze_commit",
            ),
            "individual_success_claim": False,
            "success_claim": False,
        }
    )
    write_json(metrics_path, metrics)

    annotate(
        output_dir / "data_manifest.json",
        {
            "candidate": protocol["candidate"],
            "candidate13_protocol": protocol["schema"],
            "candidate13_protocol_file": selected_protocol.name,
            "holdout": week,
        },
    )
    annotate(
        output_dir / "run.json",
        {
            "candidate": protocol["candidate"],
            "candidate13_protocol": protocol["schema"],
            "candidate13_protocol_file": selected_protocol.name,
            "holdout": week,
            "source_lock": "source_lock.json",
        },
    )

    audit_result = audit(output_dir, week)
    audit_result.update(
        {
            "schema": "candidate-13-evidence-audit-v1",
            "candidate": protocol["candidate"],
            "holdout_role": holdouts[week]["role"],
            "aggregate_gate_scope": True,
            "source_lock_passed": verified_lock["all_matched"],
        }
    )
    write_json(output_dir / "audit.json", audit_result)
    lines = [
        "# Candidate 13 evidence audit",
        "",
        f"**{audit_result['classification']}**",
        "",
    ]
    for key in (
        "evidence_complete",
        "metric_recalculation_passed",
        "risk_budget_passed",
        "global_slot_passed",
        "partial_entry_protection_passed",
        "no_liquidation_passed",
        "engine_errors_absent",
        "source_lock_passed",
    ):
        lines.append(f"- {key}: `{audit_result[key]}`")
    lines.extend(("", "## Reasons"))
    lines.extend(f"- {reason}" for reason in audit_result["reasons"])
    (output_dir / "audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    summary = {
        "candidate": protocol["candidate"],
        "candidate13_protocol": protocol["schema"],
        "week": week,
        "start": holdouts[week]["start"],
        "end_exclusive": holdouts[week]["end_exclusive"],
        "role": holdouts[week]["role"],
        "daily_geometric_growth": metrics.get("daily_geometric_growth"),
        "closed_trades": metrics.get("closed_trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "final_nav": metrics.get("final_nav"),
        "closed_trade_max_drawdown": metrics.get("closed_trade_max_drawdown"),
        "safety_audit_passed": verified_lock["all_matched"] and all(
            audit_result.get(key) is True
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
        "audit_classification": audit_result.get("classification"),
    }
    write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if audit_result["classification"] == "IMPLEMENTATION_OR_EVIDENCE_FAILURE":
        raise SystemExit(2)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("week")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "protocol.json",
    )
    args = parser.parse_args()
    execute(
        args.week,
        args.output_dir.resolve(),
        protocol_path=args.protocol,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
