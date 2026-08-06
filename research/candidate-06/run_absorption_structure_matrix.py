"""Controlled ACSR matrix through candidate-06's existing NautilusTrader runner."""

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
        "acsr_30m_full",
        "30m impact-inefficient accepted breakout -> later completed 5m opposite structure close with range, body and signed-flow agreement -> unchanged HML sweep/response execution.",
        30,
        True,
        True,
        True,
    ),
    (
        "acsr_30m_structure_only_ablation",
        "Single-variable ablation: remove only signed-flow agreement from the independent 5m opposite structure break; impact absorption and all downstream contracts remain unchanged.",
        30,
        True,
        False,
        True,
    ),
    (
        "acsr_30m_no_impact_ablation",
        "Single-variable ablation: arm every baseline-qualified 30m breakout and still require a later independent opposite 5m structure break; tests whether impact inefficiency adds causal information.",
        30,
        False,
        True,
        True,
    ),
    (
        "acsr_60m_full_horizon_reference",
        "Horizon reference: identical full ACSR logic on the inherited 60m acceptance horizon; reported for frequency and stability diagnosis, not selection.",
        60,
        True,
        True,
        False,
    ),
)


def _base(candidate_dir: Path) -> dict[str, Any]:
    base = _afhr_base(candidate_dir)
    base["logic"].update(
        {
            "engine": "ABSORPTION_CONFIRMED_STRUCTURE_REVERSAL",
            # HTF flow is classified by impact efficiency, not the legacy raw threshold.
            "hff_use_bias_flow": False,
            # HFF holdout evidence retained these two downstream causal stages.
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
            "acsr_confirmation_periods": 2.0,
            "acsr_structure_lookback_bars": 4,
            "acsr_structure_range_lookback": 8,
            "acsr_structure_break_range_fraction": 0.05,
            "acsr_structure_body_fraction": 0.50,
            "acsr_structure_relative_range": 0.80,
            "acsr_structure_flow_ratio": 0.04,
            "acsr_structure_close_location": 0.65,
            "acsr_disproof_extension_atr_htf": 0.02,
        },
    )
    return base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/acsr-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _base(candidate_dir)
    results: list[dict[str, Any]] = []
    for (
        name,
        description,
        bias_period,
        require_absorption,
        structure_flow,
        eligible,
    ) in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "hsc_bias_period_minutes": bias_period,
                "acsr_require_impact_absorption": require_absorption,
                "acsr_use_structure_flow": structure_flow,
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
                "bias_period_minutes": bias_period,
                "impact_absorption": require_absorption,
                "structure_flow": structure_flow,
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
        locked_path = candidate_dir / "config.acsr.locked.json"
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
        "candidate": "Absorption-Confirmed Structure Reversal (ACSR)",
        "design": "completed HTF accepted breakout -> direction-aligned flow has weak realized impact -> no immediate fade -> later completed 5m opposite structure close -> opposite context -> confirmed swing/equal liquidity sweep -> separate 1m response -> unchanged Nautilus execution and NAV risk",
        "market_logic": {
            "absorption_event": "aggression that fails to achieve its own prior-reference displacement is an event anchor, not a direction signal",
            "independent_structure": "reversal becomes tradable only after a later completed lower-timeframe auction closes through prior structure; the anchor bar cannot self-confirm",
            "disproof": "a completed close extending beyond the absorbed auction extreme invalidates the pending reversal thesis",
            "downstream": "after opposite context creation, pool formation, sweep, response, stop, objective, delayed entry, fees and 3% current-NAV risk remain inherited",
        },
        "controlled_contract": {
            "retained_evidence": [
                "impact efficiency suppresses the known SIAR holdout loss cluster",
                "sweep-stage and response-stage signed flow from the HFF all-flow holdout",
                "completed-close freshness from AFHR",
            ],
            "ablated_one_at_a_time": [
                "5m opposite-structure signed flow",
                "HTF impact-absorption classification",
                "30m versus inherited 60m event horizon as an ineligible reference",
            ],
            "unchanged": [
                "completed-bar causality",
                "confirmed 5m swing/equal liquidity pools",
                "counter-context sweep and separate response",
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
        "v2.0 Absorption-Confirmed Structure Reversal",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")

    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
