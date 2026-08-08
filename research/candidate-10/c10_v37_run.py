#!/usr/bin/env python3
"""Run v36 CE/equilibrium entry with v37 post-entry risk-transfer ablation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "ce-equilibrium-original-stop",
    "ce-equilibrium-internal-pivot-protection",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v37 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1"
    os.environ["C10_V36_CE_REJECTION"] = "1"
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1"
    os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] = (
        "1" if variant.endswith("internal-pivot-protection") else "0"
    )


def event_counts(path: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if not path.is_file():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            counts[str(json.loads(line).get("event_type", "UNKNOWN"))] += 1
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
    config["selection"]["weeks"]["V37"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v37_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "frozen_entry": "v36 CE touch then second rejection-displacement retrace",
        "frozen_primary_target": "source dealing-range equilibrium",
        "frozen_initial_invalidation": "actual CE-retest extreme plus frozen ATR buffer",
        "only_ablation_variable": "post-entry confirmed internal-pivot protection",
        "pivot_definition": (
            "first five-minute pivot whose event and one-right-wing confirmation "
            "both occur after real entry fill and which is a higher low/lower high "
            "relative to the CE-retest extreme"
        ),
        "protective_stop": "confirmed pivot plus the frozen Candidate 11 ATR buffer",
        "not_used": [
            "MFE threshold",
            "fixed-R breakeven",
            "elapsed-time stop",
            "new score or fitted parameter",
        ],
        "risk_fraction": 0.03,
        "post_equilibrium_runner": "not included",
        "success_claim": False,
    }
    config_path = output / "v37_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V37", output)
    metrics["week_start"] = args.week_start.isoformat()
    metrics["variant"] = args.variant
    metrics["candidate_generation"] = (
        "candidate-10-v37-confirmed-internal-pivot-protection"
    )
    metrics["v37_internal_pivot_protection_enabled"] = (
        os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] == "1"
    )
    records = list(metrics.get("cost_records", []))
    metrics["v37_internal_pivot_protection_armed_count"] = sum(
        bool(row.get("internal_pivot_protection_armed"))
        for row in records
    )
    metrics["v37_internal_pivot_protective_stops"] = [
        {
            "scenario_id": row.get("scenario_id"),
            "symbol": row.get("symbol"),
            "pivot_event_ts_ns": row.get("internal_pivot_event_ts_ns"),
            "pivot_known_ts_ns": row.get("internal_pivot_known_ts_ns"),
            "pivot_level": row.get("internal_pivot_level"),
            "reference_extreme": row.get("internal_pivot_reference_extreme"),
            "protective_stop": row.get("internal_pivot_protective_stop"),
        }
        for row in records
        if row.get("internal_pivot_protection_armed")
    ]
    counts = event_counts(output / "scenario_events.raw.jsonl")
    metrics["v37_state_event_counts"] = {
        name: counts.get(name, 0)
        for name in (
            "CE_RETEST_ARMED",
            "CE_RETEST_TOUCHED",
            "CE_REJECTION_DISPLACEMENT_CONFIRMED",
            "TRADE_PLAN_CONFIRMED",
            "ENTRY_FILLED",
            "FAVORABLE_INTERNAL_PIVOT_CONFIRMED",
            "POSITION_TERMINAL",
        )
    }
    life = lifecycle_counts(output / "order_lifecycle.json")
    metrics["v37_lifecycle_counts"] = {
        name: life.get(name, 0)
        for name in (
            "CONFIRMED_INTERNAL_PIVOT_PROTECTION_ARMED",
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
