#!/usr/bin/env python3
"""Run Candidate 11 with v31 efficient-raid factorial variants."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "baseline-far-only",
    "efficient-far",
    "efficient-far-equilibrium",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v31 variant: {variant}")
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "0"
    os.environ["C10_V30_FAR_ONLY"] = "1"
    os.environ["C10_V30_EQUILIBRIUM"] = (
        "1" if variant == "efficient-far-equilibrium" else "0"
    )
    os.environ["C10_V31_ABLATE_SWEEP_EFFICIENCY"] = (
        "1" if variant == "baseline-far-only" else "0"
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
    config["selection"]["weeks"]["V31"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v31_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "lower_layers_frozen": ["v27_cost", "v28_resolution", "v29_external_draw"],
        "far_only": True,
        "only_selector_ablation_variable": "sweep excursion efficiency",
        "efficiency_definition": "penetration_atr / relative_volume",
        "efficiency_threshold_source": "frozen LogicConfig.displacement_body_atr",
        "source_equilibrium_risk_transfer": args.variant == "efficient-far-equilibrium",
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v31_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V31", output)
    metrics["week_start"] = args.week_start.isoformat()
    metrics["variant"] = args.variant
    metrics["v31_efficiency_certificate"] = args.variant != "baseline-far-only"
    metrics["v31_equilibrium_protection"] = args.variant == "efficient-far-equilibrium"
    cost_records = list(metrics.get("cost_records", []))
    metrics["v31_equilibrium_armed_count"] = sum(
        bool(row.get("equilibrium_protection_armed"))
        for row in cost_records
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
