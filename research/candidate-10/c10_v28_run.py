#!/usr/bin/env python3
"""Run one Candidate 11 week with v28 costs and resolution certificate."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate11-dir", type=Path, required=True)
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--variant",
        choices=("full-resolved-auction", "ablation-original-leadership"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidate_dir = args.candidate11_dir.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(candidate_dir))
    os.environ["C10_V28_ABLATE_RESOLUTION"] = (
        "1" if args.variant.startswith("ablation") else "0"
    )

    source_config = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    source_config["selection"]["weeks"]["V28"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    source_config["selection"]["evaluation_days"] = 7
    source_config["v28_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "v27_cost_model_frozen": True,
        "only_ablation_variable": "resolved-auction certificate",
        "resolution_semantics": {
            "reject_market_wide_severe_adverse_FAR": True,
            "follower_relative_recovery_requires_existing_impulse_minimum": True,
        },
        "risk_fraction": 0.03,
    }
    config_path = output / "v28_config.json"
    config_path.write_text(
        json.dumps(source_config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V28", output)
    metrics["week_start"] = args.week_start.isoformat()
    metrics["variant"] = args.variant
    metrics["success_claim"] = False
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
