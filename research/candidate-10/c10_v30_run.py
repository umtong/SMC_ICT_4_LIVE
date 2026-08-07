#!/usr/bin/env python3
"""Run Candidate 11 with v30 FAR/equilibrium factorial variants."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "baseline-v29",
    "far-only",
    "far-equilibrium",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v30 variant: {variant}")
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "0"
    os.environ["C10_V30_FAR_ONLY"] = "0" if variant == "baseline-v29" else "1"
    os.environ["C10_V30_EQUILIBRIUM"] = "1" if variant == "far-equilibrium" else "0"


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
    config["selection"]["weeks"]["V30"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v30_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "v27_cost_model_frozen": True,
        "v28_resolution_certificate_frozen": True,
        "v29_independent_external_draw_frozen": True,
        "far_only": args.variant != "baseline-v29",
        "source_equilibrium_risk_transfer": args.variant == "far-equilibrium",
        "risk_transfer_level": "PREEXISTING_SOURCE_DEALING_RANGE_MIDPOINT",
        "replacement_stop": "MODELED_ALL_COST_NEUTRAL_PRICE",
        "runner_target": "ORIGINAL_INDEPENDENT_EXTERNAL_DRAW",
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v30_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V30", output)
    metrics["week_start"] = args.week_start.isoformat()
    metrics["variant"] = args.variant
    metrics["v30_far_only"] = args.variant != "baseline-v29"
    metrics["v30_equilibrium_protection"] = args.variant == "far-equilibrium"
    cost_records = list(metrics.get("cost_records", []))
    metrics["v30_equilibrium_armed_count"] = sum(
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
