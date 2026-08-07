#!/usr/bin/env python3
"""Run frozen Candidate 11 under the v35 CE-entry/target 2x2 ablation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "near-edge-to-external-draw",
    "near-edge-to-equilibrium",
    "ce-to-external-draw",
    "ce-to-equilibrium",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v35 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "0"
    os.environ["C10_V35_CE_ENTRY"] = (
        "1" if variant.startswith("ce-") else "0"
    )
    os.environ["C10_V35_EQUILIBRIUM_TARGET"] = (
        "1" if variant.endswith("equilibrium") else "0"
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
    config["selection"]["weeks"]["V35"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v35_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "scenario_family": "FAR_ONLY",
        "factorial_variables": {
            "entry": [
                "existing_near_displacement_edge",
                "displacement_void_consequent_encroachment",
            ],
            "target": [
                "independent_external_draw",
                "source_dealing_range_equilibrium",
            ],
        },
        "consequent_encroachment": "exact midpoint of confirmed displacement zone",
        "hard_stop": "original raid extreme plus frozen ATR buffer",
        "entry_expiry": "frozen Candidate 11 expiry",
        "new_fitted_thresholds": [],
        "risk_fraction": 0.03,
        "post_equilibrium_runner": "not included",
        "success_claim": False,
    }
    config_path = output / "v35_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V35", output)
    metrics["week_start"] = args.week_start.isoformat()
    metrics["variant"] = args.variant
    metrics["candidate_generation"] = "candidate-10-v35-displacement-ce"
    metrics["v35_ce_entry_enabled"] = os.environ["C10_V35_CE_ENTRY"] == "1"
    metrics["v35_equilibrium_target_enabled"] = (
        os.environ["C10_V35_EQUILIBRIUM_TARGET"] == "1"
    )
    rejections = list(metrics.get("candidate_rejections", []))
    metrics["v35_ce_rejection_counts"] = dict(
        Counter(
            str(row.get("reason", "UNKNOWN"))
            for row in rejections
            if row.get("type") == "DISPLACEMENT_CE_REJECTED"
        ),
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
