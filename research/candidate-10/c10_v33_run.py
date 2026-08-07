#!/usr/bin/env python3
"""Run frozen Candidate 11 under the v33 source-equilibrium 2x2 ablation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "baseline-external-draw-raid-stop",
    "equilibrium-target-raid-stop",
    "external-draw-zone-invalidation",
    "primary-equilibrium-zone-invalidation",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v33 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "0"
    os.environ["C10_V33_EQUILIBRIUM_TARGET"] = (
        "1"
        if variant
        in {
            "equilibrium-target-raid-stop",
            "primary-equilibrium-zone-invalidation",
        }
        else "0"
    )
    os.environ["C10_V33_ZONE_INVALIDATION"] = (
        "1"
        if variant
        in {
            "external-draw-zone-invalidation",
            "primary-equilibrium-zone-invalidation",
        }
        else "0"
    )


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
    config["selection"]["weeks"]["V33"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v33_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "frozen_detector_layers": [
            "candidate11_session_pool_identity",
            "candidate11_failed_auction_reversal",
            "v27_all_cost_current_nav_risk",
            "v28_resolved_market_auction",
            "v29_independent_external_draw",
        ],
        "scenario_family": "FAR_ONLY",
        "factorial_variables": {
            "primary_target": [
                "original_independent_external_draw",
                "source_dealing_range_equilibrium",
            ],
            "primary_invalidation": [
                "original_raid_extreme",
                "confirmation_displacement_void",
            ],
        },
        "source_equilibrium": "midpoint of source pool endpoint and paired endpoint",
        "post_equilibrium_runner": "not included; separately funded state machine required",
        "new_fitted_thresholds": [],
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v33_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V33", output)
    metrics["week_start"] = args.week_start.isoformat()
    metrics["variant"] = args.variant
    metrics["candidate_generation"] = "candidate-10-v33-source-equilibrium-primary"
    metrics["v33_equilibrium_target_enabled"] = (
        os.environ["C10_V33_EQUILIBRIUM_TARGET"] == "1"
    )
    metrics["v33_zone_invalidation_enabled"] = (
        os.environ["C10_V33_ZONE_INVALIDATION"] == "1"
    )
    rejections = list(metrics.get("candidate_rejections", []))
    reason_counts = Counter(
        str(row.get("reason", "UNKNOWN"))
        for row in rejections
        if row.get("type") == "PRIMARY_EQUILIBRIUM_REJECTED"
    )
    metrics["v33_primary_rejection_counts"] = dict(reason_counts)
    metrics["v33_cost_record_count"] = len(metrics.get("cost_records", []))
    metrics["success_claim"] = False
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
