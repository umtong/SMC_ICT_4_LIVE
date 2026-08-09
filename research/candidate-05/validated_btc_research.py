#!/usr/bin/env python3
"""Resolve the strongest exact-control BTC strategy in one sequential program.

All market replay and performance measurement are delegated to the existing
NautilusTrader runners.  This module only orders frozen experiments, separates
implementation failures from logic failures, and selects among candidates which
pass the 91-day BTC alpha gate.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from controlled_candidate_research import run_controlled_candidate
from master_research import CONTINUOUS_30D
from master_research import CONTINUOUS_91D
from master_research import NO_EARLY
from master_research import V26
from master_research import StrategySpec
from master_research import atomic_json
from master_research import btc_long_gate
from master_research import run_integrity
from master_research import run_variant


V29B = StrategySpec(
    "v29b_external_displacement_fvg",
    "strategy_v29b_external_displacement_fvg",
    "ExternalDisplacementFvgStrategyV2",
    "EXTERNAL_DISPLACEMENT_FVG_RETEST",
)
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
CANDIDATES = (V29B, V30, V31, V32)


def implementation_error(classification: str) -> bool:
    upper = classification.upper()
    return any(token in upper for token in ("IMPLEMENTATION", "EVIDENCE_ERROR"))


def metric_score(run: dict[str, Any]) -> tuple[float, float, int, int]:
    return (
        float(run.get("geometric_daily_growth", -1.0) or -1.0),
        -float(run.get("max_drawdown", 1.0) or 1.0),
        int(run.get("trades", 0) or 0),
        int(run.get("wins", 0) or 0),
    )


def baseline_family(
    *,
    output_root: Path,
    cache_root: Path,
    python: str,
) -> dict[str, Any]:
    v26_30d = run_variant(
        spec=V26,
        range_spec=CONTINUOUS_30D,
        output_root=output_root,
        cache_root=cache_root / "v26",
        python=python,
    )
    no_early_30d = run_variant(
        spec=NO_EARLY,
        range_spec=CONTINUOUS_30D,
        output_root=output_root,
        cache_root=cache_root / "no_early",
        python=python,
    )
    result: dict[str, Any] = {
        "v26_30d": v26_30d,
        "no_early_30d": no_early_30d,
        "qualified": [],
        "classification": "BASELINE_FAMILY_SCREENED",
    }
    if not run_integrity(v26_30d) or not run_integrity(no_early_30d):
        result["classification"] = "IMPLEMENTATION_OR_EVIDENCE_ERROR_BASELINE_FAMILY_30D"
        return result

    v26_eligible = float(v26_30d["geometric_daily_growth"]) >= 0.01
    no_early_eligible = (
        float(no_early_30d["geometric_daily_growth"]) >= 0.01
        and float(no_early_30d["geometric_daily_growth"])
        > float(v26_30d["geometric_daily_growth"])
    )
    result["thirty_day_decision"] = {
        "v26_eligible": v26_eligible,
        "no_early_eligible": no_early_eligible,
        "no_early_delta_geometric_daily_growth": (
            float(no_early_30d["geometric_daily_growth"])
            - float(v26_30d["geometric_daily_growth"])
        ),
        "whole_period_goal": 0.01,
    }
    if not v26_eligible and not no_early_eligible:
        result["classification"] = "BASELINE_FAMILY_BELOW_30D_GOAL"
        return result

    v26_91d: dict[str, Any] | None = None
    if v26_eligible or no_early_eligible:
        v26_91d = run_variant(
            spec=V26,
            range_spec=CONTINUOUS_91D,
            output_root=output_root,
            cache_root=cache_root / "v26",
            python=python,
        )
        result["v26_91d"] = v26_91d
        if not run_integrity(v26_91d):
            result["classification"] = "IMPLEMENTATION_OR_EVIDENCE_ERROR_V26_91D"
            return result
        gate = btc_long_gate(v26_91d)
        result["v26_91d_gate"] = gate
        if v26_eligible and gate["passed"]:
            result["qualified"].append(
                {
                    "strategy": f"{V26.module}:{V26.class_name}",
                    "source": "baseline-family",
                    "run": v26_91d,
                    "gate": gate,
                },
            )

    if no_early_eligible:
        no_early_91d = run_variant(
            spec=NO_EARLY,
            range_spec=CONTINUOUS_91D,
            output_root=output_root,
            cache_root=cache_root / "no_early",
            python=python,
        )
        result["no_early_91d"] = no_early_91d
        if not run_integrity(no_early_91d):
            result["classification"] = "IMPLEMENTATION_OR_EVIDENCE_ERROR_NO_EARLY_91D"
            return result
        control_improved = (
            v26_91d is not None
            and float(no_early_91d["geometric_daily_growth"])
            > float(v26_91d["geometric_daily_growth"])
        )
        gate = btc_long_gate(no_early_91d)
        result["no_early_91d_gate"] = {
            **gate,
            "beats_same_period_v26": control_improved,
        }
        if gate["passed"] and control_improved:
            result["qualified"].append(
                {
                    "strategy": f"{NO_EARLY.module}:{NO_EARLY.class_name}",
                    "source": "baseline-family-ablation",
                    "run": no_early_91d,
                    "gate": result["no_early_91d_gate"],
                },
            )
    if result["qualified"]:
        result["classification"] = "BASELINE_FAMILY_HAS_QUALIFIED_91D_STRATEGY"
    else:
        result["classification"] = "BASELINE_FAMILY_FAILED_91D_GATE"
    return result


def candidate_qualified(result: dict[str, Any]) -> dict[str, Any] | None:
    if result.get("classification") != "BTC_91D_ALPHA_GATE_PASSED":
        return None
    run = result.get("runs", {}).get("continuous-91d")
    if not isinstance(run, dict) or not run_integrity(run):
        return None
    return {
        "strategy": result.get("strategy"),
        "source": result.get("branch"),
        "run": run,
        "gate": result.get("long_gate"),
    }


def run_research(
    *,
    output_root: Path,
    cache_root: Path,
    summary_path: Path,
    winner_path: Path,
    python: str,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    cache_root = cache_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "schema": "candidate-05-validated-btc-research-v1",
        "source_commit": os.environ.get("GITHUB_SHA"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "engine_contract": "Every performance run uses candidate.py stage and NautilusTrader BacktestNode.",
        "candidate_order": [spec.name for spec in CANDIDATES],
        "experiments": {},
        "qualified": [],
        "winner": None,
    }

    baseline = baseline_family(
        output_root=output_root / "baseline-family",
        cache_root=cache_root / "baseline-family",
        python=python,
    )
    summary["experiments"]["baseline-family"] = baseline
    if implementation_error(str(baseline.get("classification", ""))):
        summary["classification"] = baseline["classification"]
        summary["next_action"] = (
            "Repair the baseline implementation without changing logic and rerun the identical 30/91-day ranges."
        )
    else:
        summary["qualified"].extend(baseline.get("qualified", []))
        for spec in CANDIDATES:
            result = run_controlled_candidate(
                spec=spec,
                output_root=output_root / spec.name,
                cache_root=cache_root / spec.name,
                summary_path=output_root / f"{spec.name}.json",
                python=python,
            )
            summary["experiments"][spec.name] = result
            qualified = candidate_qualified(result)
            if qualified is not None:
                summary["qualified"].append(qualified)
            classification = str(result.get("classification", ""))
            if implementation_error(classification):
                summary["classification"] = classification
                summary["next_action"] = (
                    "Repair the latest candidate implementation without changing its hypothesis, then rerun the identical frozen range."
                )
                break
        else:
            summary["classification"] = "ALL_ENCODED_BTC_FAMILIES_COMPLETED"

    if not implementation_error(str(summary.get("classification", ""))) and summary["qualified"]:
        selected = max(summary["qualified"], key=lambda item: metric_score(item["run"]))
        summary["winner"] = selected["strategy"]
        summary["selected"] = selected
        summary["classification"] = "VALIDATED_BTC_WINNER_RESOLVED"
        summary["next_action"] = (
            "Run BTC, ETH, SOL and XRP together in one NautilusTrader account with one global executable intent or position."
        )
        winner = {
            "schema": "candidate-05-validated-btc-winner-v2",
            "classification": "VALIDATED_BTC_WINNER_RESOLVED",
            "winner": selected["strategy"],
            "source_commit": os.environ.get("GITHUB_SHA"),
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "source": selected["source"],
            "metrics": selected["run"],
            "gate": selected["gate"],
            "selection_policy": "highest 91-day geometric daily growth, then lower drawdown, then trade and win count among exact-control gate passers",
        }
    else:
        if "next_action" not in summary:
            summary["next_action"] = (
                "No encoded exact-control BTC family passed the 91-day alpha gate; retain failure attribution and formulate a new orthogonal mechanism."
            )
        winner = {
            "schema": "candidate-05-validated-btc-winner-v2",
            "classification": "NO_VALIDATED_BTC_WINNER",
            "winner": None,
            "source_commit": os.environ.get("GITHUB_SHA"),
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "research_classification": summary.get("classification"),
            "next_action": summary["next_action"],
        }

    atomic_json(summary_path.resolve(), summary)
    atomic_json(winner_path.resolve(), winner)
    atomic_json(output_root / "validated_btc_research_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--winner", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    result = run_research(
        output_root=args.output,
        cache_root=args.cache,
        summary_path=args.summary,
        winner_path=args.winner,
        python=args.python,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()
