"""First-week completed 15-minute auction SAC defense experiment."""

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
        default=Path("artifacts/candidate-06/auction-15m-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    config = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    config["candidate_variant"] = "auction_15m_directional_defense"
    config["variant_description"] = (
        "Non-overlapping completed UTC quarter-hour auctions; SAC only; first retracement plus next-bar directional defense."
    )
    config["logic"].update(
        {
            "engine": "FIXED_INTERVAL_AUCTION_RELAY",
            "enable_srr": False,
            "enable_sac": True,
            "auction_period_minutes": 15,
            "auction_entry_window_minutes": 10,
            "auction_sweep_min_atr": 0.10,
            "sac_entry_confirmation": "DIRECTIONAL_BODY",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
            "cooldown_bars": 3,
            "ambiguous_cooldown_bars": 2,
        },
    )
    config_path = output / "auction_15m_directional_defense.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    first = _run(config_path, output / "auction_15m_directional_defense", 0, candidate_dir, repository)
    first.update({"name": "auction_15m_directional_defense", "description": config["variant_description"]})

    selected = first["name"] if first.get("gate_passed") else None
    frozen = []
    locked_path = None
    if selected is not None:
        locked = json.loads(config_path.read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.auction-15m.locked.json"
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
        "design": "single completed 15-minute auction horizon with previously validated directional-body defense",
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
        "v0.9 Completed 15-minute Auction SAC Defense",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")
    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
