#!/usr/bin/env python3
"""Run frozen Candidate 11 under the v36 entry-process/target 2x2 ablation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "immediate-to-external-draw",
    "immediate-to-equilibrium",
    "ce-rejection-to-external-draw",
    "ce-rejection-to-equilibrium",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v36 variant: {variant}")
    equilibrium = variant.endswith("equilibrium")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    # An equilibrium primary objective is independent of the optional external
    # runner, so the v29 external-draw certificate is exactly ablated only for
    # the two equilibrium-target cells.
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1" if equilibrium else "0"
    os.environ["C10_V36_CE_REJECTION"] = (
        "1" if variant.startswith("ce-rejection") else "0"
    )
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1" if equilibrium else "0"


def event_counts(path: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if not path.is_file():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        counts[str(value.get("event_type", "UNKNOWN"))] += 1
    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate11-dir", type=Path, required=True)
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidate_dir = args.candidate11_dir.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(candidate_dir))
    configure_variant(args.variant)

    config = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    config["selection"]["weeks"]["V36"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v36_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "scenario_family": "FAR_ONLY",
        "factorial_variables": {
            "entry_process": [
                "immediate_first_displacement_retrace",
                "CE_touch_then_second_displacement_retrace",
            ],
            "primary_target": [
                "independent_external_draw_with_v29_certificate",
                "source_dealing_range_equilibrium_without_runner_dependency",
            ],
        },
        "CE": "exact midpoint of first causal confirmation displacement zone",
        "second_confirmation": (
            "touch-bar break plus frozen displacement body, directional "
            "aggressor flow and close-location rules"
        ),
        "final_invalidation": "actual CE-retest extreme plus frozen ATR buffer",
        "entry_expiry": "frozen Candidate 11 retrace expiry",
        "new_fitted_thresholds": [],
        "risk_fraction": 0.03,
        "post_equilibrium_runner": "not included",
        "success_claim": False,
    }
    config_path = output / "v36_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V36", output)
    metrics["week_start"] = args.week_start.isoformat()
    metrics["variant"] = args.variant
    metrics["candidate_generation"] = "candidate-10-v36-ce-retest-rejection"
    metrics["v36_ce_rejection_enabled"] = (
        os.environ["C10_V36_CE_REJECTION"] == "1"
    )
    metrics["v36_equilibrium_target_enabled"] = (
        os.environ["C10_V36_EQUILIBRIUM_TARGET"] == "1"
    )
    metrics["v36_v29_external_draw_certificate_enabled"] = (
        os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] == "0"
    )
    counts = event_counts(output / "scenario_events.raw.jsonl")
    metrics["v36_state_event_counts"] = {
        name: counts.get(name, 0)
        for name in (
            "CE_RETEST_ARMED",
            "CE_RETEST_TOUCHED",
            "CE_REJECTION_DISPLACEMENT_CONFIRMED",
            "TRADE_PLAN_CONFIRMED",
            "ENTRY_FILLED",
            "POSITION_TERMINAL",
        )
    }
    metrics["success_claim"] = False
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
