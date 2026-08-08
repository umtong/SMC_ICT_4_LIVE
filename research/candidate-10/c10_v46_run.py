#!/usr/bin/env python3
"""Run v46 hard raid stop versus completed void-close failure exit."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "hard-raid-stop-only",
    "hard-raid-stop-plus-void-close-exit",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v46 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1"
    os.environ["C10_V40_SOURCE_EQUILIBRIUM_DETECTOR"] = "1"
    os.environ["C10_V36_CE_REJECTION"] = "0"
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1"
    os.environ["C10_V41_SOURCE_ENTRY_MODE"] = (
        "FIRST_DISPLACEMENT_NEAR_EDGE"
    )
    os.environ["C10_V44_PRIMARY_TARGET_MODE"] = "SOURCE_EQUILIBRIUM"
    os.environ["C10_V45_INVALIDATION_MODE"] = "SOURCE_RAID_EXTREME"
    os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_REFERENCE"] = "EXPECTED_ENTRY"
    os.environ["C10_V46_VOID_CLOSE_EXIT"] = (
        "1" if variant.endswith("void-close-exit") else "0"
    )


def count_jsonl(path: Path, key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if not path.is_file():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            counts[str(json.loads(line).get(key, "UNKNOWN"))] += 1
    return dict(counts)


def load_rows(path: Path, key: str) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get(key, [])
    return list(rows) if isinstance(rows, list) else []


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

    config = json.loads(
        (candidate_dir / "config.json").read_text(encoding="utf-8"),
    )
    config["selection"]["weeks"]["V46"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v46_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": (
            "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327"
        ),
        "frozen_detector": "v40 decoupled source-range failed auction",
        "frozen_entry": "v41 first-displacement near-edge passive retrace",
        "frozen_primary_target": "source dealing-range equilibrium",
        "hard_stop_and_sizing_basis": (
            "source raid extreme plus frozen ATR buffer, 3% current-NAV "
            "all-cost loss budget"
        ),
        "only_alpha_variable": (
            "after real fill, market exit on a completed one-minute close "
            "through the opposite edge of the first displacement void"
        ),
        "void_close_contract": {
            "LONG": "completed close <= first displacement zone low",
            "SHORT": "completed close >= first displacement zone high",
            "intrabar_touch": "does not qualify",
            "extra_buffer": 0,
            "consecutive_close_requirement": 1,
        },
        "pending_parent_contract": (
            "cancel when either the hard raid stop or source-equilibrium target "
            "trades before the passive parent obtains position ownership"
        ),
        "new_fitted_thresholds": [],
        "other_post_entry_management": "disabled",
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v46_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V46", output)
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    life_counts = Counter(str(row.get("type", "UNKNOWN")) for row in lifecycle)
    exit_events = [
        row
        for row in lifecycle
        if row.get("type")
        == "FIRST_DISPLACEMENT_VOID_CLOSE_FAILURE_EXIT_SUBMITTED"
    ]
    triggered_cost_records = [
        record
        for record in metrics.get("cost_records", [])
        if record.get("void_close_exit_triggered")
    ]
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics.update(
        {
            "week_start": args.week_start.isoformat(),
            "variant": args.variant,
            "candidate_generation": (
                "candidate-10-v46-completed-void-close-failure"
            ),
            "v46_void_close_exit_enabled": (
                os.environ["C10_V46_VOID_CLOSE_EXIT"] == "1"
            ),
            "v46_void_close_exit_count": len(exit_events),
            "v46_void_close_exit_events": exit_events,
            "v46_void_close_triggered_cost_record_count": len(
                triggered_cost_records
            ),
            "v46_pending_hard_stop_cancellation_count": life_counts.get(
                "PENDING_ENTRY_CANCELED_AFTER_INVALIDATION",
                0,
            ),
            "v46_pending_target_cancellation_count": life_counts.get(
                "PENDING_ENTRY_CANCELED_AFTER_TARGET_DELIVERY",
                0,
            ),
            "v46_protection_fail_close_reassertion_count": life_counts.get(
                "PROTECTIVE_ACTIVATION_FAIL_CLOSE_REASSERTED",
                0,
            ),
            "v46_state_event_counts": {
                name: events.get(name, 0)
                for name in (
                    "SOURCE_RANGE_LIQUIDITY_SWEEP",
                    "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
                    "TRADE_PLAN_CONFIRMED",
                    "ENTRY_FILLED",
                    "POSITION_TERMINAL",
                )
            },
            "success_claim": False,
        },
    )
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
