#!/usr/bin/env python3
"""Contract-correct entrypoint for the sequential Nautilus research run.

The underlying experiment functions live in :mod:`master_research`.  This v2
entrypoint changes only evidence serialization: slot-based dataclasses are
serialized with ``dataclasses.asdict`` rather than ``__dict__``.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from typing import Any

from master_research import CONTINUOUS_30D
from master_research import CONTINUOUS_91D
from master_research import NO_EARLY
from master_research import STRATEGY_ENTRYPOINT
from master_research import V29B
from master_research import V30
from master_research import V31
from master_research import WEEKS
from master_research import atomic_json
from master_research import controlled_candidate_pipeline
from master_research import run_integrity
from master_research import v26_control_pipeline


def run_master(
    *,
    output_root: Path,
    cache_root: Path,
    summary_path: Path,
    python: str,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    cache_root = cache_root.resolve()
    summary_path = summary_path.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    original_entrypoint = STRATEGY_ENTRYPOINT.read_text(encoding="utf-8")

    summary: dict[str, Any] = {
        "schema": "candidate-05-master-research-continuation-v2",
        "source_commit": os.environ.get("GITHUB_SHA"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "engine_contract": "Every run invokes candidate.py stage and NautilusTrader BacktestNode.",
        "risk_contract": "3% full-current-NAV planned stop-fill loss including costs; no arbitrary notional or model multiplier.",
        "fixed_ranges": [
            asdict(range_spec)
            for range_spec in (*WEEKS, CONTINUOUS_30D, CONTINUOUS_91D)
        ],
        "candidate_order": [V29B.name, V30.name, V31.name],
        "experiments": {},
        "winner": None,
    }
    try:
        baseline_weeks, v26_decision = v26_control_pipeline(
            output_root=output_root,
            cache_root=cache_root,
            python=python,
        )
        summary["experiments"]["v26_control_and_ablation"] = v26_decision
        promotion = v26_decision.get("promotion_91d", {})
        if promotion.get("eligible") and promotion.get("gate", {}).get("passed"):
            summary["winner"] = promotion["selected_strategy"]
            summary["classification"] = "BTC_91D_ALPHA_GATE_PASSED"
        elif not all(run_integrity(run) for run in baseline_weeks):
            summary["classification"] = "IMPLEMENTATION_ERROR_V26_BASELINE_WEEKS"
        else:
            v29b = controlled_candidate_pipeline(
                spec=V29B,
                baseline_weeks=baseline_weeks,
                output_root=output_root,
                cache_root=cache_root,
                python=python,
            )
            summary["experiments"][V29B.name] = v29b
            if v29b["classification"] == "BTC_91D_ALPHA_GATE_PASSED":
                summary["winner"] = v29b["strategy"]
                summary["classification"] = v29b["classification"]
            elif v29b.get("implementation_error"):
                summary["classification"] = v29b["classification"]
            else:
                v30 = controlled_candidate_pipeline(
                    spec=V30,
                    baseline_weeks=baseline_weeks,
                    output_root=output_root,
                    cache_root=cache_root,
                    python=python,
                )
                summary["experiments"][V30.name] = v30
                if v30["classification"] == "BTC_91D_ALPHA_GATE_PASSED":
                    summary["winner"] = v30["strategy"]
                    summary["classification"] = v30["classification"]
                elif v30.get("implementation_error"):
                    summary["classification"] = v30["classification"]
                else:
                    v31 = controlled_candidate_pipeline(
                        spec=V31,
                        baseline_weeks=baseline_weeks,
                        output_root=output_root,
                        cache_root=cache_root,
                        python=python,
                    )
                    summary["experiments"][V31.name] = v31
                    if v31["classification"] == "BTC_91D_ALPHA_GATE_PASSED":
                        summary["winner"] = v31["strategy"]
                    summary["classification"] = v31["classification"]

        if summary["winner"] is not None:
            summary["next_action"] = (
                "Freeze the winning BTC logic and build the real one-account four-symbol NautilusTrader competition "
                "with at most one pending entry intent or open position globally."
            )
        elif str(summary.get("classification", "")).startswith("IMPLEMENTATION"):
            summary["next_action"] = (
                "Repair the latest implementation under variable control and rerun the identical frozen range; "
                "do not interpret the candidate's market logic yet."
            )
        else:
            summary["next_action"] = (
                "Record the failed families and their useful observations, retain v26 as the active baseline, "
                "and formulate the next orthogonal alpha mechanism."
            )
        atomic_json(summary_path, summary)
        atomic_json(output_root / "master_research_summary.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return summary
    finally:
        STRATEGY_ENTRYPOINT.write_text(original_entrypoint, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    run_master(
        output_root=args.output,
        cache_root=args.cache,
        summary_path=args.summary,
        python=args.python,
    )


if __name__ == "__main__":
    main()
