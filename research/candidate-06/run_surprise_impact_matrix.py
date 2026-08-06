"""Controlled SIAR matrix through candidate-06's existing NautilusTrader runner."""

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
        "siar_full",
        "Unexpected signed aggressive-flow intensity plus realized displacement-per-surprise; completed-close freshness retained; downstream HML unchanged.",
        True,
        True,
        True,
    ),
    (
        "siar_surprise_only_ablation",
        "Single-variable ablation: remove only impact-efficiency classification while retaining prior-only flow surprise and freshness.",
        True,
        False,
        True,
    ),
    (
        "siar_impact_only_ablation",
        "Single-variable ablation: remove only the exceptional-surprise threshold while retaining directionally positive residual flow, impact efficiency and freshness.",
        False,
        True,
        True,
    ),
    (
        "siar_freshness_reference",
        "Reference: disable both new acceptance mechanisms while retaining the already-diagnosed completed-close freshness control; ineligible for selection.",
        False,
        False,
        False,
    ),
)


def _base(candidate_dir: Path) -> dict[str, Any]:
    base = _afhr_base(candidate_dir)
    base["logic"].update(
        {
            "engine": "SURPRISE_IMPACT_HIERARCHICAL",
            # The old absolute AFHR quality definition is replaced, not stacked.
            "afhr_use_adaptive_quality": False,
            "afhr_use_extreme_freshness": True,
            "afhr_stale_periods": 6.0,
            # Raw HTF flow threshold is removed; surprise becomes the HTF flow contract.
            "hff_use_bias_flow": False,
            "hff_use_sweep_flow": False,
            "hff_use_response_flow": True,
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
        default=Path("artifacts/candidate-06/siar-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _base(candidate_dir)
    results: list[dict[str, Any]] = []
    for name, description, use_surprise, use_impact, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "siar_use_flow_surprise": use_surprise,
                "siar_use_impact_efficiency": use_impact,
            },
        )
        config_path = output / f"{name}.json"
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        run_output = output / name
        record = _run(config_path, run_output, 0, candidate_dir, repository)
        record.update(
            {
                "name": name,
                "description": description,
                "flow_surprise": use_surprise,
                "impact_efficiency": use_impact,
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
        locked_path = candidate_dir / "config.siar.locked.json"
        locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
    summary = {
        "candidate": "Surprise-Impact Acceptance Relay (SIAR)",
        "design": "completed HTF structural break -> prior-only signed aggressive-flow surprise -> realized displacement-per-surprise distinguishes continuation from absorption -> fresh context -> unchanged confirmed 5m swing/equal sweep and separate response -> unchanged Nautilus execution and NAV risk",
        "market_logic": {
            "surprise": "expected flow should already be reflected in adaptive liquidity; only the residual relative to sealed prior completed auctions is fresh information",
            "impact_efficiency": "large unexpected aggressive flow that cannot produce direction-consistent displacement is treated as absorption, not continuation",
            "freshness": "accepted context still expires when completed closes stop extending in the accepted direction",
        },
        "controlled_contract": {
            "changed": [
                "HTF signed-flow evidence is expected-flow residual rather than raw flow ratio",
                "HTF continuation requires realized displacement per residual flow unless ablated",
            ],
            "unchanged": [
                "completed-bar causality",
                "minimum structural break/body/close/participation preconditions",
                "confirmed 5m swing/equal liquidity pools",
                "counter-bias sweep and separate response",
                "response-stage flow",
                "structural stop and objective",
                "one-bar delayed entry",
                "3% current-NAV risk sizing",
                "NautilusTrader fees, slippage, fills, positions and NAV",
            ],
        },
        "variant_priority": [name for name, *_ in VARIANTS],
        "selection_rule": "fixed ex-ante priority among eligible variants passing the existing complete first-week gate; unchanged selected configuration is then replayed on both sealed weeks",
        "selected": selected,
        "locked_config": None if locked_path is None else str(locked_path.relative_to(repository)),
        "first_week_results": results,
        "frozen_validation": frozen,
        "all_three_weeks_passed": all_three,
        "long_evaluation_authorized": all_three,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = _render(results, selected, frozen).replace(
        "v0.5 Session Equilibrium Retest",
        "v1.9 Surprise-Impact Acceptance Relay",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")

    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
