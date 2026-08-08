#!/usr/bin/env python3
"""Run the v41 best entry with exact v38 micro-risk ownership ablation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "near-edge-original-stop",
    "near-edge-entry-side-micro",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v42 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1"
    os.environ["C10_V40_SOURCE_EQUILIBRIUM_DETECTOR"] = "1"
    os.environ["C10_V36_CE_REJECTION"] = "0"
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1"
    os.environ["C10_V41_SOURCE_ENTRY_MODE"] = "FIRST_DISPLACEMENT_NEAR_EDGE"
    os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] = "0"
    enabled = variant == "near-edge-entry-side-micro"
    os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] = "1" if enabled else "0"
    os.environ["C10_V38_MICRO_PIVOT_REFERENCE"] = "EXPECTED_ENTRY"


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
    config["selection"]["weeks"]["V42"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v42_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "frozen_detector": "v40 decoupled source-range failed auction",
        "frozen_entry": "v41 first-displacement near-edge passive retrace",
        "frozen_target": "source dealing-range equilibrium",
        "frozen_initial_stop": "source raid extreme plus frozen ATR buffer",
        "only_ablation_variable": (
            "first one-minute right-confirmed pivot on profitable side of "
            "expected entry transfers protection behind that pivot"
        ),
        "new_parameters": [],
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v42_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V42", output)
    metrics.update(
        {
            "week_start": args.week_start.isoformat(),
            "variant": args.variant,
            "candidate_generation": (
                "candidate-10-v42-near-edge-entry-side-micro-protection"
            ),
            "v42_micro_protection_enabled": (
                os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] == "1"
            ),
        },
    )
    records = list(metrics.get("cost_records", []))
    metrics["v42_micro_protection_armed_count"] = sum(
        bool(row.get("internal_pivot_protection_armed")) for row in records
    )
    metrics["v42_protective_stops"] = [
        {
            "scenario_id": row.get("scenario_id"),
            "symbol": row.get("symbol"),
            "pivot_event_ts_ns": row.get("internal_pivot_event_ts_ns"),
            "pivot_known_ts_ns": row.get("internal_pivot_known_ts_ns"),
            "pivot_level": row.get("internal_pivot_level"),
            "entry_reference": row.get("pivot_reference_level"),
            "protective_stop": row.get("internal_pivot_protective_stop"),
        }
        for row in records
        if row.get("internal_pivot_protection_armed")
    ]
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics["v42_state_event_counts"] = {
        name: events.get(name, 0)
        for name in (
            "SOURCE_RANGE_LIQUIDITY_SWEEP",
            "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
            "TRADE_PLAN_CONFIRMED",
            "ENTRY_FILLED",
            "FAVORABLE_MICRO_PIVOT_CONFIRMED",
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
