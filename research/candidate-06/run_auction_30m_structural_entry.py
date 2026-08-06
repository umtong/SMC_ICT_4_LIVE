"""30-minute auction with structural defense and no arbitrary favorable-drift veto."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_equilibrium_matrix import _base as _equilibrium_base
from run_equilibrium_matrix import _render, _run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/auction-30m-structural-entry"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    config = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    config["candidate_variant"] = "auction_30m_structural_entry"
    config["variant_description"] = (
        "Completed 30-minute auction SAC with next-bar directional defense; entry is rejected only by causal invalidation, bracket bounds, or after-cost RR, not an arbitrary favorable-drift distance."
    )
    config["logic"].update(
        {
            "engine": "FIXED_INTERVAL_AUCTION_RELAY",
            "enable_srr": False,
            "enable_sac": True,
            "auction_period_minutes": 30,
            "auction_entry_window_minutes": 25,
            "auction_sweep_min_atr": 0.10,
            "sac_entry_confirmation": "DIRECTIONAL_BODY",
            "enforce_favorable_drift_guard": False,
            "cooldown_bars": 3,
            "ambiguous_cooldown_bars": 2,
        },
    )
    config_path = output / "auction_30m_structural_entry.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    first = _run(config_path, output / "auction_30m_structural_entry", 0, candidate_dir, repository)
    first.update({"name": "auction_30m_structural_entry", "description": config["variant_description"]})

    selected = first["name"] if first.get("gate_passed") else None
    frozen = []
    locked_path = None
    if selected is not None:
        locked = json.loads(config_path.read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.auction-30m-structural-entry.locked.json"
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
        "design": "30-minute fixed auction; directional defense fixed; arbitrary favorable-drift veto removed",
        "selection_rule": "first week must pass every gate before frozen weeks are opened",
        "selected": selected,
        "locked_config": None if locked_path is None else str(locked_path.relative_to(repository)),
        "first_week_results": [first],
        "frozen_validation": frozen,
        "all_three_weeks_passed": all_three,
        "long_evaluation_authorized": all_three,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = _render([first], selected, frozen).replace(
        "v0.5 Session Equilibrium Retest",
        "v0.7.3 30-minute Auction Structural Entry",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")
    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
