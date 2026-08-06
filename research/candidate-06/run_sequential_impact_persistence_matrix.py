"""Controlled SIPR factor matrix through the existing NautilusTrader runner."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from run_adaptive_fresh_matrix import _base as _afhr_base
from run_adaptive_fresh_matrix import _evidence
from run_equilibrium_matrix import _render, _run


VARIANTS = (
    (
        "sipr_full",
        "Two consecutive completed 15m structural acceptances in one direction; both must convert direction-aligned residual flow into prior-reference effective displacement; downstream sweep and response unchanged.",
        True,
        True,
        True,
    ),
    (
        "sipr_sequence_only_ablation",
        "Single-variable ablation: retain consecutive 15m structural acceptance and remove only impact-efficiency classification from both auctions.",
        True,
        False,
        True,
    ),
    (
        "sipr_impact_only_ablation",
        "Single-variable ablation: retain effective-impact classification and remove only the requirement for a second consecutive accepted 15m auction.",
        False,
        True,
        True,
    ),
    (
        "sipr_raw_15m_reference",
        "Reference: one baseline-qualified 15m structural acceptance with both new factors disabled; ineligible for selection.",
        False,
        False,
        False,
    ),
)


def _base(candidate_dir: Path) -> dict[str, Any]:
    base = _afhr_base(candidate_dir)
    base["logic"].update(
        {
            "engine": "SEQUENTIAL_IMPACT_PERSISTENCE_RELAY",
            "hsc_bias_period_minutes": 15,
            "hsc_liquidity_period_minutes": 5,
            "hff_use_bias_flow": False,
            "hff_use_sweep_flow": True,
            "hff_use_response_flow": True,
            "afhr_use_adaptive_quality": False,
            "afhr_use_extreme_freshness": True,
            "afhr_stale_periods": 6.0,
            "siar_use_flow_surprise": False,
            "siar_use_impact_efficiency": True,
            "siar_flow_lookback": 24,
            "siar_min_history": 12,
            "siar_surprise_quantile": 0.75,
            "siar_min_efficiency_history": 4,
        },
    )
    return base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/sipr-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _base(candidate_dir)
    results: list[dict[str, Any]] = []
    for name, description, sequence, impact, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "sipr_use_sequential_acceptance": sequence,
                "sipr_use_impact_efficiency": impact,
            },
        )
        config_path = output / f"{name}.json"
        config_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_output = output / name
        record = _run(config_path, run_output, 0, candidate_dir, repository)
        record.update(
            {
                "name": name,
                "description": description,
                "sequential_acceptance": sequence,
                "impact_efficiency": impact,
                "eligible_for_selection": eligible,
                "causal_evidence": _evidence(run_output),
                "selection_gate_passed": bool(record.get("gate_passed")),
            },
        )
        results.append(record)

    selected = next(
        (
            record["name"]
            for record in results
            if record.get("eligible_for_selection") and record.get("selection_gate_passed")
        ),
        None,
    )
    frozen: list[dict[str, Any]] = []
    locked_path: Path | None = None
    if selected is not None:
        locked = json.loads((output / f"{selected}.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.sipr.locked.json"
        locked_path.write_text(
            json.dumps(locked, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for week_index in (1, 2):
            week_output = output / f"locked-week-{week_index + 1}"
            record = _run(locked_path, week_output, week_index, candidate_dir, repository)
            record.update(
                {
                    "week_index": week_index,
                    "causal_evidence": _evidence(week_output),
                    "selection_gate_passed": bool(record.get("gate_passed")),
                },
            )
            frozen.append(record)

    all_three = (
        selected is not None
        and len(frozen) == 2
        and all(record.get("selection_gate_passed") for record in frozen)
    )
    terminal_status = (
        "THREE_WEEK_GATE_PASS"
        if all_three
        else "NO_FIRST_WEEK_GATE_PASS"
        if selected is None
        else "FROZEN_WEEK_FAILURE"
    )
    summary = {
        "candidate": "Sequential Impact Persistence Relay (SIPR)",
        "design": "first completed 15m structural acceptance -> no trade -> immediately following completed 15m auction independently accepts farther in the same direction -> both auctions have effective prior-reference price impact when enabled -> fresh context -> confirmed 5m swing/equal sweep -> separate 1m response -> unchanged Nautilus execution and NAV risk",
        "market_logic": {
            "sequence": "persistent directional order flow is informative only when liquidity does not fully neutralize it and a separate following auction continues price discovery",
            "impact": "flow persistence alone is not direction; each enabled auction must realize direction-consistent displacement per residual flow",
            "failure_handling": "a nonpersistent next auction resets the first state; it is not silently extended over a convenient gap",
            "context": "the first accepted auction suspends any older context, preventing stale trades while persistence is unresolved",
        },
        "controlled_contract": {
            "ablated_one_at_a_time": [
                "second consecutive structural acceptance",
                "impact efficiency on accepted auctions",
            ],
            "unchanged": [
                "completed-bar causality",
                "confirmed 5m swing/equal liquidity pools",
                "sweep-stage and response-stage signed flow",
                "completed-close freshness",
                "structural stop and objective selection",
                "one-bar delayed entry",
                "3% current-NAV risk sizing",
                "NautilusTrader fees, slippage, fills, positions and NAV",
            ],
        },
        "variant_priority": [name for name, *_ in VARIANTS],
        "selection_rule": "fixed ex-ante priority among eligible variants passing the existing complete first-week gate; only the unchanged selected configuration may open both sealed weeks",
        "selected": selected,
        "locked_config": None if locked_path is None else str(locked_path.relative_to(repository)),
        "first_week_results": results,
        "frozen_validation": frozen,
        "all_three_weeks_passed": all_three,
        "long_evaluation_authorized": all_three,
        "terminal_status": terminal_status,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = _render(results, selected, frozen).replace(
        "v0.5 Session Equilibrium Retest",
        "v2.1 Sequential Impact Persistence Relay",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")

    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
