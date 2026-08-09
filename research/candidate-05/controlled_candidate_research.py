#!/usr/bin/env python3
"""Run one candidate against an exact v26 branch-removal control at every stage.

This module orchestrates ``candidate.py stage`` only.  It is not a backtest
engine and contains no fill, fee, position, margin, liquidation or NAV logic.
Those remain entirely NautilusTrader-owned.
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
from master_research import STRATEGY_ENTRYPOINT
from master_research import V26
from master_research import WEEKS
from master_research import StrategySpec
from master_research import aggregate_three_weeks
from master_research import atomic_json
from master_research import branch_metrics
from master_research import btc_long_gate
from master_research import classify_week1
from master_research import run_integrity
from master_research import run_variant


def classify_continuous_control(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    branch: str,
    require_growth_goal: bool,
) -> dict[str, Any]:
    contribution = branch_metrics(candidate, branch)
    suffix = "30D" if require_growth_goal else "91D"
    if not run_integrity(baseline):
        return {
            "classification": f"IMPLEMENTATION_ERROR_BASELINE_{suffix}",
            "passed": False,
            "branch": contribution,
        }
    if not run_integrity(candidate):
        return {
            "classification": f"IMPLEMENTATION_ERROR_CANDIDATE_{suffix}",
            "passed": False,
            "branch": contribution,
        }
    if contribution["trades"] == 0:
        return {
            "classification": f"LOGIC_FAILURE_NO_INCREMENTAL_TRADES_{suffix}",
            "passed": False,
            "branch": contribution,
        }
    if contribution["net_pnl"] <= 0.0:
        return {
            "classification": f"LOGIC_FAILURE_NONPOSITIVE_INCREMENTAL_EXPECTANCY_{suffix}",
            "passed": False,
            "branch": contribution,
        }
    if float(candidate["geometric_daily_growth"]) <= float(baseline["geometric_daily_growth"]):
        return {
            "classification": f"LOGIC_FAILURE_DID_NOT_IMPROVE_CONTROL_{suffix}",
            "passed": False,
            "branch": contribution,
        }
    if require_growth_goal and float(candidate["geometric_daily_growth"]) < 0.01:
        return {
            "classification": "LOGIC_FAILURE_BELOW_GOAL_ON_CONTINUOUS_30D",
            "passed": False,
            "branch": contribution,
        }
    return {
        "classification": f"LOGIC_SCREEN_PASSED_CONTINUOUS_{suffix}",
        "passed": True,
        "branch": contribution,
        "baseline_geometric_daily_growth": baseline["geometric_daily_growth"],
        "candidate_geometric_daily_growth": candidate["geometric_daily_growth"],
    }


def run_controlled_candidate(
    *,
    spec: StrategySpec,
    output_root: Path,
    cache_root: Path,
    summary_path: Path,
    python: str,
) -> dict[str, Any]:
    if spec.branch is None:
        raise ValueError("candidate branch is required")
    output_root = output_root.resolve()
    cache_root = cache_root.resolve()
    summary_path = summary_path.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    original_entrypoint = STRATEGY_ENTRYPOINT.read_text(encoding="utf-8")
    summary: dict[str, Any] = {
        "schema": "candidate-05-controlled-candidate-research-v1",
        "source_commit": os.environ.get("GITHUB_SHA"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "candidate": {
            "name": spec.name,
            "strategy": f"{spec.module}:{spec.class_name}",
            "branch": spec.branch,
        },
        "control": f"{V26.module}:{V26.class_name}",
        "fixed_ranges": [asdict(item) for item in (*WEEKS, CONTINUOUS_30D, CONTINUOUS_91D)],
        "runs": {"baseline": {}, "candidate": {}},
        "winner": None,
    }
    try:
        baseline_week1 = run_variant(
            spec=V26,
            range_spec=WEEKS[0],
            output_root=output_root,
            cache_root=cache_root,
            python=python,
        )
        candidate_week1 = run_variant(
            spec=spec,
            range_spec=WEEKS[0],
            output_root=output_root,
            cache_root=cache_root,
            python=python,
        )
        summary["runs"]["baseline"][WEEKS[0].name] = baseline_week1
        summary["runs"]["candidate"][WEEKS[0].name] = candidate_week1
        week1 = classify_week1(
            baseline=baseline_week1,
            candidate=candidate_week1,
            branch=spec.branch,
        )
        summary["week1_decision"] = week1
        if not week1["passed"]:
            summary["classification"] = week1["classification"]
            return summary

        baseline_weeks = [baseline_week1]
        candidate_weeks = [candidate_week1]
        for range_spec in WEEKS[1:]:
            baseline = run_variant(
                spec=V26,
                range_spec=range_spec,
                output_root=output_root,
                cache_root=cache_root,
                python=python,
            )
            candidate = run_variant(
                spec=spec,
                range_spec=range_spec,
                output_root=output_root,
                cache_root=cache_root,
                python=python,
            )
            baseline_weeks.append(baseline)
            candidate_weeks.append(candidate)
            summary["runs"]["baseline"][range_spec.name] = baseline
            summary["runs"]["candidate"][range_spec.name] = candidate
        three = aggregate_three_weeks(
            baseline_runs=baseline_weeks,
            candidate_runs=candidate_weeks,
            branch=spec.branch,
        )
        summary["three_week_decision"] = three
        if not three["passed"]:
            summary["classification"] = three["classification"]
            return summary

        baseline_30d = run_variant(
            spec=V26,
            range_spec=CONTINUOUS_30D,
            output_root=output_root,
            cache_root=cache_root,
            python=python,
        )
        candidate_30d = run_variant(
            spec=spec,
            range_spec=CONTINUOUS_30D,
            output_root=output_root,
            cache_root=cache_root,
            python=python,
        )
        summary["runs"]["baseline"][CONTINUOUS_30D.name] = baseline_30d
        summary["runs"]["candidate"][CONTINUOUS_30D.name] = candidate_30d
        decision_30d = classify_continuous_control(
            baseline=baseline_30d,
            candidate=candidate_30d,
            branch=spec.branch,
            require_growth_goal=True,
        )
        summary["continuous_30d_decision"] = decision_30d
        if not decision_30d["passed"]:
            summary["classification"] = decision_30d["classification"]
            return summary

        baseline_91d = run_variant(
            spec=V26,
            range_spec=CONTINUOUS_91D,
            output_root=output_root,
            cache_root=cache_root,
            python=python,
        )
        candidate_91d = run_variant(
            spec=spec,
            range_spec=CONTINUOUS_91D,
            output_root=output_root,
            cache_root=cache_root,
            python=python,
        )
        summary["runs"]["baseline"][CONTINUOUS_91D.name] = baseline_91d
        summary["runs"]["candidate"][CONTINUOUS_91D.name] = candidate_91d
        decision_91d = classify_continuous_control(
            baseline=baseline_91d,
            candidate=candidate_91d,
            branch=spec.branch,
            require_growth_goal=False,
        )
        summary["continuous_91d_control_decision"] = decision_91d
        if not decision_91d["passed"]:
            summary["classification"] = decision_91d["classification"]
            return summary

        gate = btc_long_gate(candidate_91d)
        summary["btc_91d_alpha_gate"] = gate
        summary["classification"] = gate["classification"]
        if gate["passed"]:
            summary["winner"] = f"{spec.module}:{spec.class_name}"
        return summary
    finally:
        if "classification" not in summary:
            summary["classification"] = "IMPLEMENTATION_ERROR_CONTROLLED_RESEARCH_ORCHESTRATOR"
        if summary.get("winner"):
            summary["next_action"] = (
                "Freeze the BTC candidate and run the real one-account four-symbol NautilusTrader competition with one global executable slot."
            )
        elif str(summary["classification"]).startswith("IMPLEMENTATION"):
            summary["next_action"] = (
                "Repair the implementation without changing the hypothesis and rerun the identical frozen range."
            )
        else:
            summary["next_action"] = (
                "Record the logic failure and its useful observations; perform at most one core-variable ablation before discarding the family."
            )
        atomic_json(summary_path, summary)
        atomic_json(output_root / "controlled_candidate_summary.json", summary)
        STRATEGY_ENTRYPOINT.write_text(original_entrypoint, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--strategy-module", required=True)
    parser.add_argument("--strategy-class", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    summary = run_controlled_candidate(
        spec=StrategySpec(
            name=args.name,
            module=args.strategy_module,
            class_name=args.strategy_class,
            branch=args.branch,
        ),
        output_root=args.output,
        cache_root=args.cache,
        summary_path=args.summary,
        python=args.python,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
