#!/usr/bin/env python3
"""Resolve the authoritative Candidate 05 state after end-to-end v2 research."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def compact_run(run: Any) -> dict[str, Any] | None:
    if not isinstance(run, dict):
        return None
    keys = (
        "available",
        "integrity_pass",
        "evaluation_start",
        "evaluation_end",
        "calendar_days",
        "starting_nav",
        "ending_nav",
        "total_return",
        "geometric_daily_growth",
        "max_drawdown",
        "min_equity",
        "trades",
        "wins",
        "losses",
        "win_rate",
        "profit_factor",
        "expectancy_usdt",
        "active_days",
        "largest_winner_share",
        "liquidations",
        "global_slot_audit",
        "symbol_metrics",
        "scenario_metrics",
    )
    return {key: run.get(key) for key in keys if key in run}


def resolve(evidence_root: Path) -> dict[str, Any]:
    end_to_end = load(evidence_root / "end_to_end_research_v2.json")
    smoke = load(evidence_root / "shared_account_smoke.json")
    btc = load(evidence_root / "validated_btc_research_v2.json")
    winner = load(evidence_root / "validated_btc_winner_v2.json")
    shared = load(evidence_root / "shared_account_research_v2.json")
    legacy = load(evidence_root / "current_research_state.json")

    if end_to_end is not None:
        source = "end_to_end_research_v2.json"
        classification = str(end_to_end.get("classification", "CLASSIFICATION_NOT_RECORDED"))
        selected = end_to_end.get("winner")
        next_action = end_to_end.get("next_action")
    elif smoke is not None and str(smoke.get("classification", "")).startswith("IMPLEMENTATION"):
        source = "shared_account_smoke.json"
        classification = str(smoke.get("classification"))
        selected = None
        next_action = (
            "Repair the shared-account implementation under variable control and rerun the identical weak week."
        )
    elif legacy is not None:
        source = "current_research_state.json"
        classification = str(legacy.get("classification", "CLASSIFICATION_NOT_RECORDED"))
        selected = None
        next_action = legacy.get("next_action")
    else:
        source = None
        classification = "NO_AUTHORITATIVE_EVIDENCE"
        selected = None
        next_action = "Complete the self-contained end-to-end v2 research workflow."

    upper = classification.upper()
    implementation_error = any(
        token in upper
        for token in ("IMPLEMENTATION", "EVIDENCE_ERROR", "RUNTIME_ERROR")
    )
    project_goal_passed = classification == "PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS"
    logic_failure = (
        not implementation_error
        and any(token in upper for token in ("LOGIC", "ROBUSTNESS", "NO_VALIDATED"))
        and not project_goal_passed
    )

    shared_runs: dict[str, Any] = {}
    shared_source = shared
    if shared_source is None and end_to_end is not None:
        value = end_to_end.get("shared_account")
        shared_source = value if isinstance(value, dict) else None
    if isinstance(shared_source, dict):
        runs = shared_source.get("runs")
        if isinstance(runs, dict):
            shared_runs = {
                name: compact
                for name, run in runs.items()
                if (compact := compact_run(run)) is not None
            }

    state = {
        "schema": "candidate-05-authoritative-state-v2",
        "source_evidence": source,
        "classification": classification,
        "project_goal_passed": project_goal_passed,
        "implementation_or_evidence_error": implementation_error,
        "logic_or_robustness_failure": logic_failure,
        "selected_strategy": selected,
        "next_action": next_action,
        "validated_btc": {
            "research_classification": None if btc is None else btc.get("classification"),
            "winner_classification": None if winner is None else winner.get("classification"),
            "winner": None if winner is None else winner.get("winner"),
        },
        "shared_account": {
            "smoke_classification": None if smoke is None else smoke.get("classification"),
            "research_classification": None if shared_source is None else shared_source.get("classification"),
            "runs": shared_runs,
        },
        "project_contract": {
            "engine": "NautilusTrader only",
            "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "one_account": True,
            "risk_fraction": 0.03,
            "global_constraint": "unfilled new-entry intents plus open positions <= 1",
            "whole_period_geometric_daily_growth_goal": 0.01,
            "no_per_day_or_per_week_fixed_return_requirement": True,
        },
    }
    return state


def markdown(state: dict[str, Any]) -> str:
    lines = [
        "# Candidate 05 Authoritative Research State v2",
        "",
        f"- Source evidence: `{state.get('source_evidence')}`",
        f"- Classification: `{state.get('classification')}`",
        f"- Project goal passed: `{state.get('project_goal_passed')}`",
        f"- Implementation/evidence error: `{state.get('implementation_or_evidence_error')}`",
        f"- Logic/robustness failure: `{state.get('logic_or_robustness_failure')}`",
        f"- Selected strategy: `{state.get('selected_strategy')}`",
        "",
        "## Next action",
        "",
        str(state.get("next_action")),
        "",
        "## Shared-account stages",
        "",
        "| Stage | Integrity | Geo/day | Return | Trades | Wins | Active days | MDD | Largest winner share |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    runs = state.get("shared_account", {}).get("runs", {})
    if isinstance(runs, dict):
        for name, run in runs.items():
            lines.append(
                "| {name} | {integrity} | {geo} | {ret} | {trades} | {wins} | {active} | {mdd} | {share} |".format(
                    name=name,
                    integrity=run.get("integrity_pass"),
                    geo=run.get("geometric_daily_growth"),
                    ret=run.get("total_return"),
                    trades=run.get("trades"),
                    wins=run.get("wins"),
                    active=run.get("active_days"),
                    mdd=run.get("max_drawdown"),
                    share=run.get("largest_winner_share"),
                ),
            )
    lines.extend([
        "",
        "The shared stages are produced by one account and one BacktestNode per range; independent symbol accounts are not summed.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    state = resolve(args.evidence_root.resolve())
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(state), encoding="utf-8")
    print(json.dumps(state, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
