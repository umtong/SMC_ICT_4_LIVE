#!/usr/bin/env python3
"""Continue exact-control research after a master winner is invalidated.

Every candidate run is delegated to :mod:`controlled_candidate_research`, which
in turn invokes ``candidate.py stage`` and NautilusTrader.  This file contains
only experiment ordering and stop decisions.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from controlled_candidate_research import run_controlled_candidate
from master_research import StrategySpec
from master_research import atomic_json


V30 = StrategySpec(
    "v30_external_acceptance_retest",
    "strategy_v30_external_acceptance_retest",
    "ExternalAcceptanceFirstRetestStrategy",
    "EXTERNAL_ACCEPTANCE_FIRST_RETEST",
)
V31 = StrategySpec(
    "v31_impact_resiliency_reversal",
    "strategy_v31_impact_resiliency_reversal",
    "ImpactResiliencyReversalStrategy",
    "EXTERNAL_IMPACT_RESILIENCY_REVERSAL",
)
V32 = StrategySpec(
    "v32_queue_pressure_release",
    "strategy_v32_queue_pressure_release",
    "QueuePressureReleaseStrategy",
    "QUEUE_PRESSURE_CONFIRMED_RELEASE",
)

WINNER_INDEX = {
    "strategy_v29b_external_displacement_fvg:ExternalDisplacementFvgStrategyV2": 0,
    "strategy_v30_external_acceptance_retest:ExternalAcceptanceFirstRetestStrategy": 1,
    "strategy_v31_impact_resiliency_reversal:ImpactResiliencyReversalStrategy": 2,
}
CANDIDATES = (V30, V31, V32)


def run_continuation(
    *,
    audit_path: Path,
    output_root: Path,
    cache_root: Path,
    summary_path: Path,
    python: str,
) -> dict[str, Any]:
    audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {
        "classification": "MISSING_MASTER_WINNER_AUDIT",
        "selection": {},
        "master_winner_validated": False,
    }
    classification = str(audit.get("classification", ""))
    winner = audit.get("selection", {}).get("master_winner")
    summary: dict[str, Any] = {
        "schema": "candidate-05-post-audit-continuation-v1",
        "source_commit": os.environ.get("GITHUB_SHA"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "audit_classification": classification,
        "invalidated_master_winner": winner,
        "experiments": {},
        "winner": None,
    }

    if audit.get("master_winner_validated", False):
        summary["classification"] = "NOT_RUN_MASTER_WINNER_ALREADY_VALIDATED"
        summary["next_action"] = "Proceed to the one-account four-symbol NautilusTrader competition."
        atomic_json(summary_path, summary)
        return summary
    if any(token in classification.upper() for token in ("IMPLEMENTATION", "EVIDENCE_ERROR")):
        summary["classification"] = "NOT_RUN_AUDIT_HAS_IMPLEMENTATION_OR_EVIDENCE_ERROR"
        summary["next_action"] = "Repair the audit implementation and rerun the identical selected winner."
        atomic_json(summary_path, summary)
        return summary
    start = WINNER_INDEX.get(winner)
    if start is None:
        summary["classification"] = "NOT_RUN_NO_INCREMENTAL_MASTER_WINNER_TO_CONTINUE_FROM"
        summary["next_action"] = "Follow the authoritative master evidence."
        atomic_json(summary_path, summary)
        return summary

    output_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    for spec in CANDIDATES[start:]:
        candidate_summary_path = output_root / f"{spec.name}.json"
        result = run_controlled_candidate(
            spec=spec,
            output_root=output_root / spec.name,
            cache_root=cache_root / spec.name,
            summary_path=candidate_summary_path,
            python=python,
        )
        summary["experiments"][spec.name] = result
        if result.get("winner"):
            summary["winner"] = result["winner"]
            summary["classification"] = "BTC_91D_ALPHA_GATE_PASSED"
            summary["next_action"] = (
                "Freeze the exact-control BTC winner and run the one-account four-symbol NautilusTrader competition."
            )
            break
        result_classification = str(result.get("classification", ""))
        if result_classification.startswith("IMPLEMENTATION"):
            summary["classification"] = result_classification
            summary["next_action"] = (
                "Repair the latest candidate implementation without changing its hypothesis and rerun the identical range."
            )
            break
    else:
        last = summary["experiments"].get(CANDIDATES[-1].name, {})
        summary["classification"] = str(last.get("classification", "NO_CANDIDATE_RESULT"))
        summary["next_action"] = (
            "All encoded continuation, impact-resiliency and queue-pressure families failed exact controls; retain v26 and formulate a new orthogonal mechanism."
        )

    atomic_json(summary_path, summary)
    atomic_json(output_root / "post_audit_continuation_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    summary = run_continuation(
        audit_path=args.audit.resolve(),
        output_root=args.output.resolve(),
        cache_root=args.cache.resolve(),
        summary_path=args.summary.resolve(),
        python=args.python,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
