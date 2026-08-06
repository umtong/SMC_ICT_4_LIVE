"""First-week matrix for accepted-expansion pullback retests."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from run_equilibrium_matrix import _base as _equilibrium_base
from run_equilibrium_matrix import _render, _run


VARIANTS = (
    (
        "aepr_60m_compression_release",
        "Completed 60-minute accepted expansion after a compressed source auction, followed by a delayed boundary retest and separate response.",
        60,
        True,
    ),
    (
        "aepr_30m_compression_release",
        "Completed 30-minute accepted expansion after a compressed source auction, followed by a delayed boundary retest and separate response.",
        30,
        True,
    ),
    (
        "aepr_60m_all_expansions",
        "Every qualified completed 60-minute accepted expansion may establish a retest boundary.",
        60,
        False,
    ),
    (
        "aepr_30m_all_expansions",
        "Every qualified completed 30-minute accepted expansion may establish a retest boundary.",
        30,
        False,
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/aepr-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    base["logic"].update(
        {
            "engine": "ACCEPTED_EXPANSION_PULLBACK",
            "aepr_atr_bars": 12,
            "aepr_volume_bars": 12,
            "aepr_compression_bars": 12,
            "aepr_source_compression_ratio": 0.85,
            "aepr_expansion_range_atr": 0.90,
            "aepr_expansion_body_fraction": 0.55,
            "aepr_expansion_relative_volume": 1.0,
            "aepr_expansion_flow_ratio": 0.04,
            "aepr_expansion_close_location": 0.72,
            "aepr_acceptance_close_atr": 0.03,
            "aepr_bias_lifetime_periods": 3.0,
            "aepr_bias_invalidation_fraction": 0.50,
            "aepr_retest_band_atr": 0.12,
            "aepr_response_body_atr_1m": 0.12,
            "aepr_response_flow_ratio": 0.0,
            "aepr_response_close_location": 0.55,
            "aepr_response_mode": "BODY_FLOW",
            "aepr_stop_buffer_atr": 0.05,
            "aepr_extension_fraction": 0.75,
            "minimum_structural_rr": 1.10,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
        },
    )

    results = []
    for name, description, period, compression in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"]["aepr_period_minutes"] = period
        config["logic"]["aepr_require_source_compression"] = compression
        path = output / f"{name}.json"
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record = _run(path, output / name, 0, candidate_dir, repository)
        record.update(
            {
                "name": name,
                "description": description,
                "period_minutes": period,
                "compression_required": compression,
            },
        )
        results.append(record)

    selected = next((record["name"] for record in results if record.get("gate_passed")), None)
    frozen = []
    locked_path = None
    if selected is not None:
        locked = json.loads((output / f"{selected}.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.aepr.locked.json"
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
        "design": "completed accepted higher-timeframe expansion -> frozen prior-range boundary -> later retest -> separate one-minute response -> structural objective",
        "variant_priority": [name for name, *_ in VARIANTS],
        "selection_rule": "first complete gate-qualified causal horizon in fixed priority",
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
        "v1.1 Accepted Expansion Pullback Retest",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")
    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
