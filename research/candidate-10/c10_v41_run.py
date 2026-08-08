#!/usr/bin/env python3
"""Run v41 source-equilibrium entry-timing exact comparison."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "first-displacement-near-edge",
    "first-displacement-ce",
    "second-rejection-displacement",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v41 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1"
    os.environ["C10_V40_SOURCE_EQUILIBRIUM_DETECTOR"] = "1"
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1"
    os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_REFERENCE"] = "EXPECTED_ENTRY"

    if variant == "second-rejection-displacement":
        os.environ["C10_V36_CE_REJECTION"] = "1"
        mode = "SECOND_REJECTION_DISPLACEMENT"
    elif variant == "first-displacement-ce":
        os.environ["C10_V36_CE_REJECTION"] = "0"
        mode = "FIRST_DISPLACEMENT_CE"
    else:
        os.environ["C10_V36_CE_REJECTION"] = "0"
        mode = "FIRST_DISPLACEMENT_NEAR_EDGE"
    os.environ["C10_V41_SOURCE_ENTRY_MODE"] = mode


def count_jsonl(path: Path, key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if not path.is_file():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            counts[str(json.loads(line).get(key, "UNKNOWN"))] += 1
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
    config["selection"]["weeks"]["V41"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v41_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "frozen_detector": (
            "v40 regional source sweep, reclaim, post-sweep MSS and directional "
            "displacement independent of external draw"
        ),
        "frozen_primary_target": "source dealing-range equilibrium",
        "frozen_initial_invalidation": "source raid extreme plus frozen ATR buffer",
        "only_variable": "causal passive entry state after failed-auction confirmation",
        "entry_states": {
            "first-displacement-near-edge": (
                "nearest favorable edge of first confirmation displacement void"
            ),
            "first-displacement-ce": (
                "exact 50 percent consequent encroachment of first confirmation void"
            ),
            "second-rejection-displacement": (
                "CE touch, second rejection displacement, then its near-edge retrace"
            ),
        },
        "post_entry_protection": "disabled for entry attribution",
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v41_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V41", output)
    metrics.update(
        {
            "week_start": args.week_start.isoformat(),
            "variant": args.variant,
            "candidate_generation": (
                "candidate-10-v41-source-equilibrium-entry-timing"
            ),
            "v41_source_entry_mode": os.environ["C10_V41_SOURCE_ENTRY_MODE"],
            "v41_decoupled_source_detector": True,
        },
    )
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics["v41_state_event_counts"] = {
        name: events.get(name, 0)
        for name in (
            "SOURCE_RANGE_LIQUIDITY_SWEEP",
            "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
            "CE_RETEST_ARMED",
            "CE_RETEST_TOUCHED",
            "CE_REJECTION_DISPLACEMENT_CONFIRMED",
            "TRADE_PLAN_CONFIRMED",
            "ENTRY_FILLED",
            "POSITION_TERMINAL",
        )
    }
    metrics["v41_entry_timing_rejection_counts"] = dict(Counter(
        str(item.get("reason", "UNKNOWN"))
        for item in metrics.get("candidate_rejections", [])
        if item.get("type") == "SOURCE_ENTRY_TIMING_REJECTED"
    ))
    metrics["success_claim"] = False
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
