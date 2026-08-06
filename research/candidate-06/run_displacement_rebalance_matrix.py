"""First-week matrix for displacement-imbalance-rebalance continuation."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from run_equilibrium_matrix import _base as _equilibrium_base
from run_equilibrium_matrix import _render, _run


VARIANTS = (
    (
        "dirc_strict_fvg",
        "Completed five-minute displacement creates a strict three-bar fair-value gap before rebalance and response.",
        "STRICT_FVG",
    ),
    (
        "dirc_displacement_origin",
        "Completed five-minute displacement is rebalanced into the open-to-midpoint body-origin zone before response.",
        "DISPLACEMENT_BODY_ORIGIN",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/dirc-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    base["logic"].update(
        {
            "engine": "FIVE_MINUTE_DISPLACEMENT_REBALANCE",
            "dirc_aggregate_minutes": 5,
            "dirc_atr_bars": 12,
            "dirc_volume_bars": 12,
            "dirc_displacement_body_atr": 0.80,
            "dirc_displacement_body_fraction": 0.65,
            "dirc_displacement_relative_volume": 1.15,
            "dirc_displacement_flow_ratio": 0.08,
            "dirc_displacement_close_location": 0.75,
            "dirc_projection_fraction": 1.0,
            "dirc_rebalance_bars": 24,
            "dirc_response_body_atr_1m": 0.12,
            "dirc_response_flow_ratio": 0.0,
            "dirc_response_close_location": 0.55,
            "dirc_stop_buffer_atr5": 0.05,
            "minimum_structural_rr": 1.25,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
        },
    )

    results = []
    for name, description, zone_mode in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"]["dirc_zone_mode"] = zone_mode
        path = output / f"{name}.json"
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record = _run(path, output / name, 0, candidate_dir, repository)
        record.update({"name": name, "description": description, "zone_mode": zone_mode})
        results.append(record)

    selected = next((record["name"] for record in results if record.get("gate_passed")), None)
    frozen = []
    locked_path = None
    if selected is not None:
        locked = json.loads((output / f"{selected}.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.dirc.locked.json"
        locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for week_index in (1, 2):
            record = _run(
                locked_path,
                output / f"locked-week-{week_index + 1}",
                week_index,
                candidate_dir,
                repository,
            )
            record["week_index"] = week_index
            frozen.append(record)

    all_three = selected is not None and len(frozen) == 2 and all(record.get("gate_passed") for record in frozen)
    summary = {
        "design": "completed five-minute displacement -> causal imbalance zone -> later rebalance -> separate one-minute response -> structural objective",
        "variant_priority": [name for name, _, _ in VARIANTS],
        "selection_rule": "first complete gate-qualified causal zone definition in fixed priority",
        "selected": selected,
        "locked_config": None if locked_path is None else str(locked_path.relative_to(repository)),
        "first_week_results": results,
        "frozen_validation": frozen,
        "all_three_weeks_passed": all_three,
        "long_evaluation_authorized": all_three,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = _render(results, selected, frozen).replace(
        "v0.5 Session Equilibrium Retest",
        "v1.0 Displacement Imbalance Rebalance Continuation",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")
    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
