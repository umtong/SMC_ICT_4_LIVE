#!/usr/bin/env python3
"""Run v40 detector/scenario-separation 2x2 ablation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "draw-coupled-original-stop",
    "draw-coupled-entry-side-micro",
    "source-decoupled-original-stop",
    "source-decoupled-entry-side-micro",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v40 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1"
    os.environ["C10_V36_CE_REJECTION"] = "1"
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1"
    os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] = "0"
    micro = variant.endswith("entry-side-micro")
    decoupled = variant.startswith("source-decoupled")
    os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] = "1" if micro else "0"
    os.environ["C10_V38_MICRO_PIVOT_REFERENCE"] = "EXPECTED_ENTRY"
    os.environ["C10_V40_SOURCE_EQUILIBRIUM_DETECTOR"] = (
        "1" if decoupled else "0"
    )


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
    config["selection"]["weeks"]["V40"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v40_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "frozen_source": "completed regional source dealing range",
        "frozen_sweep_evidence": (
            "source-boundary trade-through with existing activity, penetration "
            "and aggressor-flow requirements"
        ),
        "frozen_failed_auction_confirmation": (
            "source reclaim, post-sweep internal MSS, displacement body and "
            "directional aggressor flow"
        ),
        "frozen_entry": "v36 CE retest then second rejection-displacement retrace",
        "frozen_primary_target": "source dealing-range equilibrium",
        "factorial_variables": {
            "detector_target_coupling": [
                "original external-draw-framed detector",
                "source sweep/reclaim/MSS detector independent of external draw",
            ],
            "post_entry_protection": [
                "original stop",
                "one-minute right-confirmed pivot on profitable side of entry",
            ],
        },
        "external_draw_in_decoupled_primary": "not observed or required",
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v40_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V40", output)
    metrics.update(
        {
            "week_start": args.week_start.isoformat(),
            "variant": args.variant,
            "candidate_generation": (
                "candidate-10-v40-source-equilibrium-detector-separation"
            ),
            "v40_source_detector_decoupled": (
                os.environ["C10_V40_SOURCE_EQUILIBRIUM_DETECTOR"] == "1"
            ),
            "v40_entry_side_micro_protection": (
                os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] == "1"
            ),
        },
    )
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics["v40_state_event_counts"] = {
        name: events.get(name, 0)
        for name in (
            "LIQUIDITY_SWEEP",
            "SOURCE_RANGE_LIQUIDITY_SWEEP",
            "FAR_CONFIRMED",
            "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
            "CE_RETEST_ARMED",
            "CE_RETEST_TOUCHED",
            "CE_REJECTION_DISPLACEMENT_CONFIRMED",
            "TRADE_PLAN_CONFIRMED",
            "ENTRY_FILLED",
            "FAVORABLE_MICRO_PIVOT_CONFIRMED",
            "POSITION_TERMINAL",
        )
    }
    records = list(metrics.get("cost_records", []))
    metrics["v40_micro_protection_armed_count"] = sum(
        bool(row.get("internal_pivot_protection_armed")) for row in records
    )
    metrics["success_claim"] = False
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
