"""Predeclared v0.8 multi-timescale liquidity relay experiments in NautilusTrader."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from run_equilibrium_matrix import _base as _equilibrium_base
from run_equilibrium_matrix import _render, _run


VARIANTS = (
    ("composite_full", "External session relay plus completed-hour auction relay; fixed session priority.", {}),
    ("composite_srr_only", "Ablation: reversal families only across both timescales.", {"enable_sac": False}),
    ("composite_sac_only", "Ablation: continuation families only across both timescales.", {"enable_srr": False}),
    ("composite_price_only", "Ablation: price structure without directional taker-buy proxy.", {"session_use_flow_proxy": False}),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-06/composite-matrix"))
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    base["logic"].update(
        {
            "engine": "MULTI_TIMESCALE_LIQUIDITY_RELAY",
            "london_range_start_minute_utc": 420,
            "london_range_end_minute_utc": 720,
            "session_use_london_levels": True,
            "auction_entry_window_minutes": 55,
            "auction_sweep_min_atr": 0.10,
            "cooldown_bars": 3,
            "ambiguous_cooldown_bars": 2,
        }
    )
    results = []
    for name, description, overrides in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(overrides)
        path = output / f"{name}.json"
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record = _run(path, output / name, 0, candidate_dir, repository)
        record.update({"name": name, "description": description})
        results.append(record)

    selected = next((record["name"] for record in results if record.get("gate_passed")), None)
    frozen = []
    locked_path = None
    if selected is not None:
        locked = json.loads((output / f"{selected}.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.composite.locked.json"
        locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for week_index in (1, 2):
            record = _run(locked_path, output / f"locked-week-{week_index + 1}", week_index, candidate_dir, repository)
            record["week_index"] = week_index
            frozen.append(record)

    all_three = selected is not None and len(frozen) == 2 and all(record.get("gate_passed") for record in frozen)
    summary = {
        "design": "fixed-priority external-session and completed-hour relay composition",
        "variant_priority": [name for name, _, _ in VARIANTS],
        "selection_rule": "first gate-qualified variant in fixed priority",
        "selected": selected,
        "locked_config": None if locked_path is None else str(locked_path.relative_to(repository)),
        "first_week_results": results,
        "frozen_validation": frozen,
        "all_three_weeks_passed": all_three,
        "long_evaluation_authorized": all_three,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "SUMMARY.md").write_text(_render(results, selected, frozen).replace("v0.5 Session Equilibrium Retest", "v0.8 Multi-timescale Liquidity Relay"), encoding="utf-8")
    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
