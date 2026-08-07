#!/usr/bin/env python3
"""Candidate 14 protocol runner and independent evidence audit."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_audit import audit
from run_leadership_scdam import run


LOCKED_FILES = (
    "bar_adapter.py",
    "global_allocator.py",
    "logic.py",
    "market_leadership.py",
    "session_engine.py",
    "run_leadership_scdam.py",
    "run_leadership_scdam_base.py",
    "runner_materializer.py",
    "semantic_execution.py",
    "semantic_logic.py",
    "semantic_market_leadership.py",
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


def source_lock(protocol: dict[str, Any]) -> dict[str, Any]:
    expected = protocol["locked_source"]["blobs"]
    actual: dict[str, Any] = {}
    for name in LOCKED_FILES:
        path = ROOT / name
        payload = path.read_bytes()
        actual[name] = {
            "bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "origin_git_blob": expected[name],
        }
    return {
        "schema": "candidate-14-source-lock-v1",
        "candidate": protocol["candidate"],
        "validation_mode": protocol["validation_mode"],
        "origin_branch": protocol["locked_source"]["origin_branch"],
        "files": actual,
    }


def annotate(path: Path, updates: dict[str, Any]) -> None:
    if not path.is_file():
        return
    payload = load_object(path)
    payload.update(updates)
    write_json(path, payload)


def execute(week: str, output_dir: Path) -> dict[str, Any]:
    protocol = load_object(ROOT / "protocol.json")
    holdouts = protocol["selection"]["holdouts"]
    if week not in holdouts:
        raise ValueError(f"unknown protocol interval {week!r}; expected one of {sorted(holdouts)}")

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
    config["candidate14_protocol"] = {
        "schema": protocol["schema"],
        "validation_mode": protocol["validation_mode"],
        "interval": week,
        "role": holdouts[week]["role"],
        "aggregate_gate": protocol["aggregate_gate"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    effective_config = output_dir / "effective_config.json"
    write_json(effective_config, config)
    write_json(output_dir / "source_lock.json", source_lock(protocol))

    metrics = run(effective_config, week, output_dir)
    metrics_path = output_dir / "metrics.json"
    if metrics_path.is_file():
        metrics = load_object(metrics_path)
    metrics.update(
        {
            "candidate": protocol["candidate"],
            "candidate14_protocol": protocol["schema"],
            "validation_mode": protocol["validation_mode"],
            "holdout_role": holdouts[week]["role"],
            "source_origin_branch": protocol["locked_source"]["origin_branch"],
            "individual_success_claim": False,
            "success_claim": False,
        }
    )
    write_json(metrics_path, metrics)

    annotate(
        output_dir / "data_manifest.json",
        {
            "candidate": protocol["candidate"],
            "candidate14_protocol": protocol["schema"],
            "validation_mode": protocol["validation_mode"],
            "interval": week,
        },
    )
    annotate(
        output_dir / "run.json",
        {
            "candidate": protocol["candidate"],
            "candidate14_protocol": protocol["schema"],
            "validation_mode": protocol["validation_mode"],
            "interval": week,
            "source_lock": "source_lock.json",
        },
    )

    audit_result = audit(output_dir, week)
    audit_result.update(
        {
            "schema": "candidate-14-evidence-audit-v1",
            "candidate": protocol["candidate"],
            "validation_mode": protocol["validation_mode"],
            "holdout_role": holdouts[week]["role"],
            "aggregate_gate_scope": True,
        }
    )
    write_json(output_dir / "audit.json", audit_result)
    lines = [
        "# Candidate 14 evidence audit",
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
    ):
        lines.append(f"- {key}: `{audit_result[key]}`")
    lines.extend(("", "## Reasons"))
    lines.extend(f"- {reason}" for reason in audit_result["reasons"])
    (output_dir / "audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    summary = {
        "candidate": protocol["candidate"],
        "validation_mode": protocol["validation_mode"],
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
        "scenario_counts": metrics.get("scenario_counts", {}),
        "leadership_rejection_counts": metrics.get("leadership_rejection_counts", {}),
        "safety_audit_passed": all(
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
    args = parser.parse_args()
    execute(args.week, args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
