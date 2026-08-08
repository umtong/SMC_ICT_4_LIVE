#!/usr/bin/env python3
"""Run the v44 source-equilibrium versus internal-liquidity target ablation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "source-equilibrium-primary",
    "preconfirmed-internal-liquidity-primary",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v44 variant: {variant}")
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
        if variant.startswith("preconfirmed")
        else "SOURCE_EQUILIBRIUM"
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
    config["selection"]["weeks"]["V44"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v44_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": (
            "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327"
        ),
        "frozen_detector": "v40 decoupled source-range failed auction",
        "frozen_entry": "v41 first-displacement near-edge passive retrace",
        "frozen_initial_invalidation": (
            "source raid extreme plus frozen ATR buffer"
        ),
        "only_alpha_variable": "primary delivery objective",
        "target_contracts": {
            "source-equilibrium-primary": (
                "exact midpoint of the completed source dealing range"
            ),
            "preconfirmed-internal-liquidity-primary": (
                "nearest still-live five-minute internal high or low already "
                "right-confirmed before plan observation, strictly between "
                "entry and source equilibrium, with the frozen all-cost R floor"
            ),
        },
        "internal_target_fallback": "reject; never silently use midpoint",
        "internal_target_search": (
            "nearest price first, then next farther only when the nearer level "
            "was already delivered or cannot clear frozen min_net_r"
        ),
        "pending_entry_target_delivery": (
            "cancel the passive parent if the active primary target is delivered "
            "before any position is acquired; applied identically to both cells"
        ),
        "new_fitted_thresholds": [],
        "post_entry_management": "none",
        "source_equilibrium_runner": "not included in this attribution",
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v44_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V44", output)
    plans = load_rows(output / "submitted_plans.json", "plans")
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    hierarchy_plans: list[dict[str, object]] = []
    for row in plans:
        details = row.get("details", {})
        if not isinstance(details, dict):
            continue
        hierarchy = details.get("source_target_hierarchy")
        if not isinstance(hierarchy, dict) or not hierarchy.get("applied"):
            continue
        hierarchy_plans.append(
            {
                "scenario_id": row.get("scenario_id"),
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "entry": row.get("entry"),
                "stop": row.get("stop"),
                "selected_internal_liquidity": hierarchy.get(
                    "selected_internal_liquidity"
                ),
                "source_equilibrium": hierarchy.get("source_equilibrium"),
                "selected_internal_event_ts_ns": hierarchy.get(
                    "selected_internal_event_ts_ns"
                ),
                "selected_internal_known_ts_ns": hierarchy.get(
                    "selected_internal_known_ts_ns"
                ),
                "candidate_count": hierarchy.get(
                    "candidate_count_between_entry_and_equilibrium"
                ),
                "selected_costed_structural_r_before_impact": hierarchy.get(
                    "selected_costed_structural_r_before_impact"
                ),
            },
        )

    target_rejections = [
        row
        for row in metrics.get("candidate_rejections", [])
        if row.get("type") == "SOURCE_TARGET_HIERARCHY_REJECTED"
    ]
    rejection_counts = Counter(
        str(row.get("reason", "UNKNOWN")) for row in target_rejections
    )
    life_counts = Counter(str(row.get("type", "UNKNOWN")) for row in lifecycle)
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics.update(
        {
            "week_start": args.week_start.isoformat(),
            "variant": args.variant,
            "candidate_generation": (
                "candidate-10-v44-causal-target-hierarchy"
            ),
            "v44_primary_target_mode": os.environ[
                "C10_V44_PRIMARY_TARGET_MODE"
            ],
            "v44_internal_target_submitted_count": len(hierarchy_plans),
            "v44_internal_target_submitted": hierarchy_plans,
            "v44_target_hierarchy_rejection_counts": dict(rejection_counts),
            "v44_pending_entry_target_cancellation_count": life_counts.get(
                "PENDING_ENTRY_CANCELED_AFTER_TARGET_DELIVERY",
                0,
            ),
            "v44_state_event_counts": {
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
