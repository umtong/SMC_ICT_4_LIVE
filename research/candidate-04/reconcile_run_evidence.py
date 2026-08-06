#!/usr/bin/env python3
"""Reduce one GitHub Actions run's artifacts to a durable research record.

This is an evidence utility, not a backtest engine.  It never calculates fills,
PnL or NAV.  It preserves metrics already produced from NautilusTrader evidence
and records whether a run ended before an economic decision was available.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DECISION_NAMES = {
    "research_decision.json",
    "final_decision.json",
    "sequential_gate.json",
}
SUMMARY_NAMES = {"summary.json", "aggregate.json", "comparison.json"}


def load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def score_decision(path: Path, value: dict[str, Any]) -> tuple[int, int, int]:
    final = int(path.name == "final_decision.json")
    explicit = int(
        any(
            key in value
            for key in (
                "decision",
                "project_target_reached",
                "candidate_pass",
                "three_week_pass",
                "three_unopened_weeks_pass",
                "development_pass",
            )
        )
    )
    depth = len(path.parts)
    return final, explicit, depth


def summarize_tree(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    if not root.exists():
        return decisions, summaries
    for path in sorted(root.rglob("*.json")):
        value = load_json(path)
        if not isinstance(value, dict):
            continue
        item = {"path": str(path.relative_to(root)), "value": value}
        if path.name in DECISION_NAMES:
            decisions.append(item)
        elif path.name in SUMMARY_NAMES and any(
            key in value
            for key in (
                "trades",
                "total_return",
                "geometric_daily_growth",
                "weeks",
                "candidate_pass",
            )
        ):
            summaries.append(item)
    return decisions, summaries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--conclusion", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    decisions, summaries = summarize_tree(args.artifacts)
    primary: dict[str, Any] | None = None
    if decisions:
        primary = max(
            decisions,
            key=lambda item: score_decision(
                Path(item["path"]),
                item["value"],
            ),
        )
    elif summaries:
        primary = max(
            summaries,
            key=lambda item: (
                int("weeks" in item["value"]),
                int("candidate_pass" in item["value"]),
                len(Path(item["path"]).parts),
            ),
        )

    economic_decision_available = bool(primary)
    if not economic_decision_available:
        failure_classification = "implementation_or_workflow"
    elif args.conclusion != "success":
        # A decision may deliberately reject a candidate while the workflow
        # itself succeeds.  A failed workflow with a completed economic summary
        # is still retained, but marked mixed for manual audit.
        failure_classification = "mixed_run_failure_with_preserved_economic_evidence"
    else:
        failure_classification = "economic_or_gate_result"

    record = {
        "workflow": args.workflow,
        "run_id": args.run_id,
        "head_sha": args.head_sha,
        "workflow_conclusion": args.conclusion,
        "artifact_root": str(args.artifacts),
        "economic_decision_available": economic_decision_available,
        "failure_classification": failure_classification,
        "primary_evidence": primary,
        "decisions": decisions,
        "summaries": summaries,
        "evidence_contract": {
            "pnl_source": "NautilusTrader-produced artifacts only",
            "reconciler_calculates_pnl": False,
            "run_specific_path": True,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(record, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
