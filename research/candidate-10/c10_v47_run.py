#!/usr/bin/env python3
"""Run the v47 event-leader by void-failure exact 2x2 attribution."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "all-approved-hard-stop",
    "all-approved-void-close",
    "event-leader-hard-stop",
    "event-leader-void-close",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v47 variant: {variant}")
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
        "1" if variant.endswith("void-close") else "0"
    )
    os.environ["C10_V47_EVENT_LEADER_ONLY"] = (
        "1" if variant.startswith("event-leader") else "0"
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
    config["selection"]["weeks"]["V47"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v47_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": (
            "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327"
        ),
        "frozen_detector": "v40 decoupled source-range failed auction",
        "frozen_entry": "v41 first-displacement near-edge passive retrace",
        "frozen_primary_target": "source dealing-range equilibrium",
        "frozen_hard_stop_and_sizing": (
            "source raid extreme plus frozen ATR buffer and current-NAV 3% "
            "all-cost loss budget"
        ),
        "factorial_variables": {
            "market_state_router": [
                "all plans approved by frozen market-leadership gate",
                "candidate event-direction rank must equal one",
            ],
            "post_fill_failure": [
                "hard source-raid stop only",
                "completed one-minute close through first displacement void",
            ],
        },
        "event_rank_contract": (
            "one plus the count of BTC/ETH/SOL/XRP peers with a larger "
            "direction-signed sweep-to-confirmation return, computed from "
            "synchronized completed observations available at confirmation"
        ),
        "event_leader_contract": (
            "rank equals one; ties are handled by the frozen rank definition"
        ),
        "not_the_same_as": "trailing quote-notional leader identity",
        "new_fitted_thresholds": [],
        "risk_multiplier": "none",
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v47_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V47", output)
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    plans = load_rows(output / "submitted_plans.json", "plans")
    life_counts = Counter(str(row.get("type", "UNKNOWN")) for row in lifecycle)
    rank_counts: Counter[str] = Counter()
    accepted_router_details: list[dict[str, object]] = []
    for row in plans:
        details = row.get("details", {})
        if not isinstance(details, dict):
            continue
        leadership = details.get("market_leadership", {})
        router = details.get("event_direction_leader_router", {})
        if not isinstance(leadership, dict):
            leadership = {}
        if not isinstance(router, dict):
            router = {}
        rank = leadership.get("event_direction_rank")
        rank_counts[str(rank)] += 1
        accepted_router_details.append(
            {
                "scenario_id": row.get("scenario_id"),
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "event_direction_rank": rank,
                "candidate_event_move": leadership.get(
                    "candidate_event_move"
                ),
                "peer_event_median": leadership.get("peer_event_median"),
                "quote_notional_leader": leadership.get("leader"),
                "router_applied": router.get("applied"),
                "entry": row.get("entry"),
                "stop": row.get("stop"),
                "target": row.get("target"),
                "net_r": row.get("net_r"),
            },
        )

    router_rejections = [
        row
        for row in metrics.get("candidate_rejections", [])
        if row.get("type") == "EVENT_DIRECTION_LEADER_REJECTED"
    ]
    rejection_reasons = Counter(
        str(row.get("reason", "UNKNOWN")) for row in router_rejections
    )
    rejected_ranks = Counter(
        str(row.get("event_direction_rank")) for row in router_rejections
    )
    exit_events = [
        row
        for row in lifecycle
        if row.get("type")
        == "FIRST_DISPLACEMENT_VOID_CLOSE_FAILURE_EXIT_SUBMITTED"
    ]
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics.update(
        {
            "week_start": args.week_start.isoformat(),
            "variant": args.variant,
            "candidate_generation": (
                "candidate-10-v47-event-direction-leader-router"
            ),
            "v47_event_leader_only_enabled": (
                os.environ["C10_V47_EVENT_LEADER_ONLY"] == "1"
            ),
            "v47_void_close_exit_enabled": (
                os.environ["C10_V46_VOID_CLOSE_EXIT"] == "1"
            ),
            "v47_accepted_event_rank_counts": dict(rank_counts),
            "v47_accepted_router_details": accepted_router_details,
            "v47_event_router_rejection_count": len(router_rejections),
            "v47_event_router_rejection_reasons": dict(rejection_reasons),
            "v47_event_router_rejected_rank_counts": dict(rejected_ranks),
            "v47_void_close_exit_count": len(exit_events),
            "v47_void_close_exit_events": exit_events,
            "v47_pending_hard_stop_cancellation_count": life_counts.get(
                "PENDING_ENTRY_CANCELED_AFTER_INVALIDATION",
                0,
            ),
            "v47_pending_target_cancellation_count": life_counts.get(
                "PENDING_ENTRY_CANCELED_AFTER_TARGET_DELIVERY",
                0,
            ),
            "v47_state_event_counts": {
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
