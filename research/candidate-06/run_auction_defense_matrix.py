"""Controlled SAC order-time defense experiments on the first BTC week."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from run_equilibrium_matrix import _base as _equilibrium_base
from run_equilibrium_matrix import _render, _run


VARIANTS = (
    (
        "defense_directional_body",
        "The next completed bar must still hold the accepted boundary and close in the continuation direction.",
        "DIRECTIONAL_BODY",
    ),
    (
        "defense_directional_flow",
        "The next completed bar must still hold the accepted boundary with same-direction taker-flow imbalance.",
        "DIRECTIONAL_FLOW",
    ),
    (
        "defense_body_and_flow",
        "The next completed bar must hold the boundary with both directional body and directional flow.",
        "BODY_AND_FLOW",
    ),
    (
        "defense_reference_hold",
        "The next completed bar must retain or exceed the retest-confirmation close.",
        "REFERENCE_HOLD",
    ),
    (
        "defense_reference_hold_and_flow",
        "The next completed bar must retain the retest close and same-direction flow.",
        "REFERENCE_HOLD_AND_FLOW",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/auction-defense-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    base["logic"].update(
        {
            "engine": "ROLLING_AUCTION_LIQUIDITY_RELAY",
            "enable_srr": False,
            "enable_sac": True,
            "auction_entry_window_minutes": 55,
            "auction_sweep_min_atr": 0.10,
            "cooldown_bars": 3,
            "ambiguous_cooldown_bars": 2,
        }
    )

    results = []
    for name, description, mode in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"]["sac_entry_confirmation"] = mode
        path = output / f"{name}.json"
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record = _run(path, output / name, 0, candidate_dir, repository)
        record.update({"name": name, "description": description, "confirmation_mode": mode})
        results.append(record)

    selected = next((record["name"] for record in results if record.get("gate_passed")), None)
    frozen = []
    locked_path = None
    if selected is not None:
        locked = json.loads((output / f"{selected}.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.auction-defense.locked.json"
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
        "design": "same 60-minute SAC auction; only the next-bar defense state is varied",
        "variant_priority": [name for name, _, _ in VARIANTS],
        "selection_rule": "first complete gate-qualified causal mode in fixed priority",
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
        "v0.7.1 Hourly SAC Next-Bar Defense",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")
    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
