#!/usr/bin/env python3
"""Run v38 post-entry structure-resolution exact comparisons."""
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
    "five-minute-retest-pivot",
    "one-minute-retest-pivot",
    "one-minute-entry-side-pivot",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v38 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1"
    os.environ["C10_V36_CE_REJECTION"] = "1"
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1"
    os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] = (
        "1" if variant == "five-minute-retest-pivot" else "0"
    )
    micro = variant.startswith("one-minute")
    os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] = "1" if micro else "0"
    os.environ["C10_V38_MICRO_PIVOT_REFERENCE"] = (
        "EXPECTED_ENTRY"
        if variant == "one-minute-entry-side-pivot"
        else "CE_RETEST_EXTREME"
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
    config["selection"]["weeks"]["V38"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v38_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "frozen_entry": "v36 CE touch then second rejection-displacement retrace",
        "frozen_primary_target": "source dealing-range equilibrium",
        "frozen_initial_invalidation": "actual CE-retest extreme plus frozen ATR buffer",
        "only_changes": [
            "pivot observation timeframe",
            "micro-pivot reference: CE-retest extreme or expected entry",
        ],
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v38_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V38", output)
    metrics.update(
        {
            "week_start": args.week_start.isoformat(),
            "variant": args.variant,
            "candidate_generation": (
                "candidate-10-v38-confirmed-one-minute-micro-pivot-protection"
            ),
            "v38_five_minute_protection_enabled": (
                os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] == "1"
            ),
            "v38_one_minute_protection_enabled": (
                os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] == "1"
            ),
            "v38_micro_pivot_reference": os.environ[
                "C10_V38_MICRO_PIVOT_REFERENCE"
            ],
        },
    )
    records = list(metrics.get("cost_records", []))
    metrics["v38_protection_armed_count"] = sum(
        bool(row.get("internal_pivot_protection_armed")) for row in records
    )
    metrics["v38_protective_stops"] = [
        {
            "scenario_id": row.get("scenario_id"),
            "symbol": row.get("symbol"),
            "pivot_timeframe": row.get("pivot_timeframe"),
            "pivot_reference_contract": row.get("pivot_reference_contract"),
            "pivot_reference_level": row.get("pivot_reference_level"),
            "pivot_event_ts_ns": row.get("internal_pivot_event_ts_ns"),
            "pivot_known_ts_ns": row.get("internal_pivot_known_ts_ns"),
            "pivot_level": row.get("internal_pivot_level"),
            "protective_stop": row.get("internal_pivot_protective_stop"),
        }
        for row in records
        if row.get("internal_pivot_protection_armed")
    ]
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics["v38_state_event_counts"] = {
        name: events.get(name, 0)
        for name in (
            "CE_RETEST_ARMED",
            "CE_RETEST_TOUCHED",
            "CE_REJECTION_DISPLACEMENT_CONFIRMED",
            "ENTRY_FILLED",
            "FAVORABLE_INTERNAL_PIVOT_CONFIRMED",
            "FAVORABLE_MICRO_PIVOT_CONFIRMED",
            "POSITION_TERMINAL",
        )
    }
    life = lifecycle_counts(output / "order_lifecycle.json")
    metrics["v38_lifecycle_counts"] = {
        name: life.get(name, 0)
        for name in (
            "CONFIRMED_INTERNAL_PIVOT_PROTECTION_ARMED",
            "CONFIRMED_MICRO_PIVOT_PROTECTION_ARMED",
            "MODELED_IMPACT_DEBITED",
            "V37_REPLACEMENT_SUBMISSION_EXCEPTION",
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
