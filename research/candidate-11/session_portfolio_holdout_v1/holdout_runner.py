#!/usr/bin/env python3
"""Run one source-locked untouched holdout with NautilusTrader.

The strategy is imported from ``session_portfolio_v1`` and is never copied or
modified here.  Every Git blob in the pre-data protocol is verified before the
first downloader call.  This runner only binds an untouched interval to the
already-frozen configuration and writes independently auditable evidence.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CANDIDATE_ROOT = HERE.parent
STRATEGY_ROOT = CANDIDATE_ROOT / "session_portfolio_v1"
if str(STRATEGY_ROOT) not in sys.path:
    sys.path.insert(0, str(STRATEGY_ROOT))

from evidence_audit import audit  # noqa: E402
from run_leadership_scdam import run  # noqa: E402


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


def git_blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def verify_source_lock(protocol: dict[str, Any]) -> dict[str, Any]:
    expected = protocol["locked_source"]["blobs"]
    records: dict[str, Any] = {}
    mismatches: dict[str, Any] = {}
    for name, expected_blob in expected.items():
        path = STRATEGY_ROOT / name
        payload = path.read_bytes()
        actual_blob = git_blob(path)
        records[name] = {
            "bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "expected_git_blob": expected_blob,
            "actual_git_blob": actual_blob,
        }
        if actual_blob != expected_blob:
            mismatches[name] = {
                "expected": expected_blob,
                "actual": actual_blob,
            }
    if mismatches:
        raise SystemExit(f"strategy source changed after holdout freeze: {mismatches}")
    return {
        "schema": "candidate-11-multi-session-source-lock-v1",
        "candidate": protocol["candidate"],
        "validation_mode": protocol["validation_mode"],
        "source_commit_before_market_data": protocol["source_commit_before_market_data"],
        "diagnostic_evidence_commit": protocol["diagnostic_evidence_commit"],
        "files": records,
    }


def annotate(path: Path, updates: dict[str, Any]) -> None:
    if not path.is_file():
        return
    payload = load_object(path)
    payload.update(updates)
    write_json(path, payload)


def execute(interval: str, output_dir: Path) -> dict[str, Any]:
    protocol = load_object(HERE / "holdout_protocol.json")
    if protocol.get("market_data_opened") is not False:
        raise SystemExit("pre-data protocol marker is not intact")
    holdouts = protocol["selection"]["holdouts"]
    if interval not in holdouts:
        raise ValueError(f"unknown holdout {interval!r}; expected one of {sorted(holdouts)}")

    source_record = verify_source_lock(protocol)
    config = load_object(STRATEGY_ROOT / "base_config.json")
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
    config["session_i7"] = load_object(STRATEGY_ROOT / "session_i7_config.json")
    config["candidate11_holdout_protocol"] = {
        "schema": protocol["schema"],
        "validation_mode": protocol["validation_mode"],
        "interval": interval,
        "role": holdouts[interval]["role"],
        "aggregate_gate": protocol["aggregate_gate"],
        "source_commit_before_market_data": protocol["source_commit_before_market_data"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    effective_config = output_dir / "effective_config.json"
    write_json(effective_config, config)
    write_json(output_dir / "source_lock.json", source_record)

    metrics = run(effective_config, interval, output_dir)
    metrics_path = output_dir / "metrics.json"
    if metrics_path.is_file():
        metrics = load_object(metrics_path)
    metrics.update(
        {
            "candidate": protocol["candidate"],
            "candidate11_holdout_protocol": protocol["schema"],
            "validation_mode": protocol["validation_mode"],
            "holdout_role": holdouts[interval]["role"],
            "source_commit_before_market_data": protocol["source_commit_before_market_data"],
            "individual_success_claim": False,
            "success_claim": False,
        }
    )
    write_json(metrics_path, metrics)

    common = {
        "candidate": protocol["candidate"],
        "candidate11_holdout_protocol": protocol["schema"],
        "validation_mode": protocol["validation_mode"],
        "interval": interval,
    }
    annotate(output_dir / "data_manifest.json", common)
    annotate(
        output_dir / "run.json",
        {
            **common,
            "source_lock": "source_lock.json",
            "source_commit_before_market_data": protocol["source_commit_before_market_data"],
        },
    )

    audit_result = audit(output_dir, interval)
    audit_result.update(
        {
            "schema": "candidate-11-multi-session-holdout-audit-v1",
            "candidate": protocol["candidate"],
            "validation_mode": protocol["validation_mode"],
            "holdout_role": holdouts[interval]["role"],
            "aggregate_gate_scope": True,
        }
    )
    write_json(output_dir / "audit.json", audit_result)
    lines = [
        "# Candidate 11 untouched holdout evidence audit",
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

    safety = all(
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
    )
    summary = {
        "candidate": protocol["candidate"],
        "validation_mode": protocol["validation_mode"],
        "interval": interval,
        "start": holdouts[interval]["start"],
        "end_exclusive": holdouts[interval]["end_exclusive"],
        "role": holdouts[interval]["role"],
        "daily_geometric_growth": metrics.get("daily_geometric_growth"),
        "closed_trades": metrics.get("closed_trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "final_nav": metrics.get("final_nav"),
        "closed_trade_max_drawdown": metrics.get("closed_trade_max_drawdown"),
        "submitted_plans": metrics.get("submitted_plans"),
        "scenario_counts": metrics.get("scenario_counts", {}),
        "module_counts": metrics.get("module_counts", {}),
        "symbol_counts": metrics.get("symbol_counts", {}),
        "leadership_rejection_counts": metrics.get("leadership_rejection_counts", {}),
        "skip_reasons": metrics.get("skip_reasons", {}),
        "safety_audit_passed": safety,
        "audit_classification": audit_result.get("classification"),
        "success_claim": False,
    }
    write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if audit_result["classification"] == "IMPLEMENTATION_OR_EVIDENCE_FAILURE":
        raise SystemExit(2)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("interval")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    execute(args.interval, args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
