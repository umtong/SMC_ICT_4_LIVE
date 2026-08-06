"""Controlled continuation-stop experiment on the first BTC week."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from run_equilibrium_matrix import _base as _equilibrium_base
from run_equilibrium_matrix import _render, _run


VARIANTS = (
    (
        "stop_retest_boundary",
        "Existing continuation invalidation beyond the retest low/high and accepted boundary.",
        "RETEST_BOUNDARY",
    ),
    (
        "stop_acceptance_impulse_origin",
        "Continuation invalidation beyond both the acceptance displacement origin and first retest.",
        "ACCEPTANCE_IMPULSE_ORIGIN",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/auction-structural-stop-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    base["logic"].update(
        {
            "engine": "ROLLING_AUCTION_STRUCTURAL_STOP",
            "enable_srr": False,
            "enable_sac": True,
            "auction_entry_window_minutes": 55,
            "auction_sweep_min_atr": 0.10,
            "sac_entry_confirmation": "DIRECTIONAL_BODY",
            "enforce_favorable_drift_guard": True,
            "cooldown_bars": 3,
            "ambiguous_cooldown_bars": 2,
        },
    )

    results = []
    for name, description, mode in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"]["continuation_stop_mode"] = mode
        path = output / f"{name}.json"
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record = _run(path, output / name, 0, candidate_dir, repository)
        record.update(
            {
                "name": name,
                "description": description,
                "continuation_stop_mode": mode,
            },
        )
        results.append(record)

    selected = next((record["name"] for record in results if record.get("gate_passed")), None)
    frozen = []
    locked_path = None
    if selected is not None:
        locked = json.loads((output / f"{selected}.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.auction-structural-stop.locked.json"
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
        "design": "same 60-minute SAC auction and order-time defense; only the causal continuation invalidation anchor changes",
        "variant_priority": [name for name, _, _ in VARIANTS],
        "selection_rule": "first complete gate-qualified causal stop mode in fixed priority",
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
        "v0.7.4 Hourly SAC Structural Invalidation",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")
    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
