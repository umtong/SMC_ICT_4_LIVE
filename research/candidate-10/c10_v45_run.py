#!/usr/bin/env python3
"""Run the v45 target-by-invalidation exact 2x2 attribution."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "raid-stop-source-equilibrium",
    "void-stop-source-equilibrium",
    "raid-stop-internal-liquidity",
    "void-stop-internal-liquidity",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v45 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1"
    os.environ["C10_V40_SOURCE_EQUILIBRIUM_DETECTOR"] = "1"
    os.environ["C10_V36_CE_REJECTION"] = "0"
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1"
    os.environ["C10_V41_SOURCE_ENTRY_MODE"] = (
        "FIRST_DISPLACEMENT_NEAR_EDGE"
    )
    os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_REFERENCE"] = "EXPECTED_ENTRY"
    os.environ["C10_V44_PRIMARY_TARGET_MODE"] = (
        "PRECONFIRMED_INTERNAL_LIQUIDITY"
        if variant.endswith("internal-liquidity")
        else "SOURCE_EQUILIBRIUM"
    )
    os.environ["C10_V45_INVALIDATION_MODE"] = (
        "FIRST_DISPLACEMENT_VOID_FAR_EDGE"
        if variant.startswith("void-stop")
        else "SOURCE_RAID_EXTREME"
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
    config["selection"]["weeks"]["V45"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v45_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": (
            "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327"
        ),
        "frozen_detector": "v40 decoupled source-range failed auction",
        "frozen_entry": "v41 first-displacement near-edge passive retrace",
        "factorial_variables": {
            "invalidation": [
                "source raid extreme plus frozen ATR buffer",
                "opposite edge of first displacement execution void plus the "
                "same frozen ATR buffer",
            ],
            "primary_target": [
                "source dealing-range equilibrium",
                "nearest live preconfirmed five-minute internal liquidity "
                "between entry and source equilibrium",
            ],
        },
        "void_stop_contract": (
            "must be strictly tighter than the source raid stop, beyond entry, "
            "above the frozen minimum stop ATR and above frozen min_net_r"
        ),
        "internal_target_contract": (
            "known before plan observation, undelivered after confirmation, "
            "nearest price first and frozen min_net_r; no fallback"
        ),
        "pending_parent_contract": (
            "cancel when either its active invalidation or target trades before "
            "the passive parent obtains position ownership"
        ),
        "new_fitted_thresholds": [],
        "post_entry_management": "none",
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v45_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V45", output)
    plans = load_rows(output / "submitted_plans.json", "plans")
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    plan_contracts: list[dict[str, object]] = []
    for row in plans:
        details = row.get("details", {})
        if not isinstance(details, dict):
            continue
        invalidation = details.get("source_entry_leg_invalidation", {})
        hierarchy = details.get("source_target_hierarchy", {})
        if not isinstance(invalidation, dict):
            invalidation = {}
        if not isinstance(hierarchy, dict):
            hierarchy = {}
        plan_contracts.append(
            {
                "scenario_id": row.get("scenario_id"),
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "entry": row.get("entry"),
                "stop": row.get("stop"),
                "target": row.get("target"),
                "net_r": row.get("net_r"),
                "void_stop_applied": bool(invalidation.get("applied")),
                "original_source_raid_stop": invalidation.get(
                    "original_source_raid_stop"
                ),
                "void_structural_boundary": invalidation.get(
                    "structural_boundary"
                ),
                "risk_atr": invalidation.get("risk_atr"),
                "internal_target_applied": bool(hierarchy.get("applied")),
                "source_equilibrium": hierarchy.get("source_equilibrium"),
                "selected_internal_liquidity": hierarchy.get(
                    "selected_internal_liquidity"
                ),
            },
        )

    rejections = list(metrics.get("candidate_rejections", []))
    invalidation_rejections = Counter(
        str(row.get("reason", "UNKNOWN"))
        for row in rejections
        if row.get("type") == "SOURCE_ENTRY_LEG_INVALIDATION_REJECTED"
    )
    target_rejections = Counter(
        str(row.get("reason", "UNKNOWN"))
        for row in rejections
        if row.get("type") == "SOURCE_TARGET_HIERARCHY_REJECTED"
    )
    life_counts = Counter(str(row.get("type", "UNKNOWN")) for row in lifecycle)
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics.update(
        {
            "week_start": args.week_start.isoformat(),
            "variant": args.variant,
            "candidate_generation": (
                "candidate-10-v45-entry-leg-invalidation-target-factorial"
            ),
            "v45_invalidation_mode": os.environ[
                "C10_V45_INVALIDATION_MODE"
            ],
            "v45_primary_target_mode": os.environ[
                "C10_V44_PRIMARY_TARGET_MODE"
            ],
            "v45_plan_contracts": plan_contracts,
            "v45_void_stop_submitted_count": sum(
                bool(row.get("void_stop_applied")) for row in plan_contracts
            ),
            "v45_internal_target_submitted_count": sum(
                bool(row.get("internal_target_applied"))
                for row in plan_contracts
            ),
            "v45_invalidation_rejection_counts": dict(
                invalidation_rejections
            ),
            "v45_target_rejection_counts": dict(target_rejections),
            "v45_pending_invalidation_cancellation_count": life_counts.get(
                "PENDING_ENTRY_CANCELED_AFTER_INVALIDATION",
                0,
            ),
            "v45_pending_target_cancellation_count": life_counts.get(
                "PENDING_ENTRY_CANCELED_AFTER_TARGET_DELIVERY",
                0,
            ),
            "v45_state_event_counts": {
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
