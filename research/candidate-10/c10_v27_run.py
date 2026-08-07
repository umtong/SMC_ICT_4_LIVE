#!/usr/bin/env python3
"""Run one untouched Candidate 11 market-leadership week with v27 costs."""
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
        choices=("full-market-leadership", "ablation-market-leadership-removed"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidate_dir = args.candidate11_dir.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(candidate_dir))
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = (
        "1" if args.variant.startswith("ablation") else "0"
    )

    source_config = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    source_config["selection"]["weeks"]["V27"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    source_config["selection"]["evaluation_days"] = 7
    source_config["v27_evaluation_contract"] = {
        "variant": args.variant,
        "week_selected_before_market_data_download": True,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "logic_frozen": True,
        "only_ablation_variable": "market leadership approval",
        "risk_fraction": 0.03,
        "cost_model": (
            "existing maker/taker rates plus causal size-dependent square-root "
            "impact debited at actual fills"
        ),
    }
    config_path = output / "v27_config.json"
    config_path.write_text(
        json.dumps(source_config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V27", output)
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
