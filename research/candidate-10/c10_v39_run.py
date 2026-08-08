#!/usr/bin/env python3
"""Run v39 entry-auction acceptance and micro-pivot 2x2 ablation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "original-stop",
    "entry-side-micro-pivot",
    "fill-bar-acceptance",
    "combined-acceptance-micro-pivot",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v39 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1"
    os.environ["C10_V36_CE_REJECTION"] = "1"
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1"
    os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] = "0"
    micro = variant in {
        "entry-side-micro-pivot",
        "combined-acceptance-micro-pivot",
    }
    acceptance = variant in {
        "fill-bar-acceptance",
        "combined-acceptance-micro-pivot",
    }
    os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] = "1" if micro else "0"
    os.environ["C10_V38_MICRO_PIVOT_REFERENCE"] = "EXPECTED_ENTRY"
    os.environ["C10_V39_ENTRY_AUCTION_ACCEPTANCE"] = (
        "1" if acceptance else "0"
    )


def count_jsonl(path: Path, key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if not path.is_file():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            counts[str(json.loads(line).get(key, "UNKNOWN"))] += 1
    return dict(counts)


def lifecycle_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(Counter(str(row.get("type", "UNKNOWN")) for row in value.get("events", [])))


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
    config["selection"]["weeks"]["V39"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v39_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "frozen_entry": "v36 CE touch then second rejection-displacement retrace",
        "frozen_primary_target": "source dealing-range equilibrium",
        "frozen_initial_invalidation": "actual CE-retest extreme plus frozen ATR buffer",
        "factorial_variables": {
            "entry_auction_acceptance": [
                "disabled",
                "first completed fill bar closes on predicted side of expected entry",
            ],
            "post_entry_micro_protection": [
                "disabled",
                "first one-minute right-confirmed pivot on profitable side of expected entry",
            ],
        },
        "acceptance_buffer": 0.0,
        "new_fitted_thresholds": [],
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v39_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V39", output)
    metrics.update(
        {
            "week_start": args.week_start.isoformat(),
            "variant": args.variant,
            "candidate_generation": "candidate-10-v39-entry-auction-acceptance",
            "v39_entry_auction_acceptance_enabled": (
                os.environ["C10_V39_ENTRY_AUCTION_ACCEPTANCE"] == "1"
            ),
            "v39_entry_side_micro_pivot_enabled": (
                os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] == "1"
            ),
        },
    )
    records = list(metrics.get("cost_records", []))
    metrics["v39_entry_auction_evaluated_count"] = sum(
        bool(row.get("entry_auction_evaluated")) for row in records
    )
    metrics["v39_entry_auction_accepted_count"] = sum(
        row.get("entry_auction_accepted") is True for row in records
    )
    metrics["v39_entry_auction_failed_count"] = sum(
        row.get("entry_auction_accepted") is False for row in records
    )
    metrics["v39_entry_auction_records"] = [
        {
            "scenario_id": row.get("scenario_id"),
            "symbol": row.get("symbol"),
            "fill_ts_ns": row.get("entry_auction_fill_ts_ns"),
            "observed_ts_ns": row.get("entry_auction_observed_ts_ns"),
            "accepted": row.get("entry_auction_accepted"),
            "boundary": row.get("entry_auction_boundary"),
            "completed_close": row.get("entry_auction_completed_close"),
            "distance_from_boundary": row.get(
                "entry_auction_distance_from_boundary",
            ),
        }
        for row in records
        if row.get("entry_auction_evaluated")
    ]
    metrics["v39_micro_protection_armed_count"] = sum(
        bool(row.get("internal_pivot_protection_armed")) for row in records
    )
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics["v39_state_event_counts"] = {
        name: events.get(name, 0)
        for name in (
            "ENTRY_FILLED",
            "ENTRY_AUCTION_ACCEPTANCE_CONFIRMED",
            "ENTRY_AUCTION_HOLD_FAILED",
            "FAVORABLE_MICRO_PIVOT_CONFIRMED",
            "POSITION_TERMINAL",
        )
    }
    life = lifecycle_counts(output / "order_lifecycle.json")
    metrics["v39_lifecycle_counts"] = {
        name: life.get(name, 0)
        for name in (
            "ENTRY_AUCTION_ACCEPTED",
            "ENTRY_AUCTION_FAILED_EXIT_SUBMITTED",
            "CONFIRMED_MICRO_PIVOT_PROTECTION_ARMED",
            "MODELED_IMPACT_DEBITED",
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
