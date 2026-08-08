#!/usr/bin/env python3
"""Run the independent AAC family with and without event-rank-one routing."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "aac-all-approved",
    "aac-event-leader",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v48 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "0"
    os.environ["C10_V48_AAC_EVENT_LEADER_ONLY"] = (
        "1" if variant == "aac-event-leader" else "0"
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
    config["selection"]["weeks"]["V48"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v48_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": (
            "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327"
        ),
        "scenario_family": "AAC_ONLY",
        "frozen_aac_state_sequence": [
            "EXTERNAL_LIQUIDITY_TRADE_THROUGH",
            "SUSTAINED_OUTSIDE_ACCEPTANCE",
            "RIGHT_CONFIRMED_CAUSAL_PULLBACK",
            "DIRECTIONAL_REACCELERATION",
            "FIRST_EXECUTION_VOID_PASSIVE_RETRACE",
        ],
        "frozen_target": (
            "pre-existing independent external liquidity hazard in the "
            "accepted direction"
        ),
        "frozen_invalidation": (
            "accepted boundary or causal pullback extreme plus frozen ATR buffer"
        ),
        "only_alpha_variable": (
            "candidate event-direction rank equals one among synchronized "
            "BTC/ETH/SOL/XRP observations available at confirmation"
        ),
        "rank_threshold": "ordinal equality to one; no return threshold",
        "non_aac_plans": "rejected as a separate-family attribution control",
        "new_fitted_thresholds": [],
        "risk_multiplier": "none",
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v48_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V48", output)
    plans = load_rows(output / "submitted_plans.json", "plans")
    rank_counts: Counter[str] = Counter()
    plan_details: list[dict[str, object]] = []
    for row in plans:
        details = row.get("details", {})
        if not isinstance(details, dict):
            continue
        leadership = details.get("market_leadership", {})
        router = details.get("aac_event_direction_leader_router", {})
        if not isinstance(leadership, dict):
            leadership = {}
        if not isinstance(router, dict):
            router = {}
        rank = leadership.get("event_direction_rank")
        rank_counts[str(rank)] += 1
        plan_details.append(
            {
                "scenario_id": row.get("scenario_id"),
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "event_direction_rank": rank,
                "trailing_direction_rank": leadership.get(
                    "trailing_direction_rank"
                ),
                "candidate_event_move": leadership.get(
                    "candidate_event_move"
                ),
                "peer_event_median": leadership.get("peer_event_median"),
                "event_path_efficiency": leadership.get(
                    "event_path_efficiency"
                ),
                "event_standardized_displacement": leadership.get(
                    "event_standardized_displacement"
                ),
                "confirmation_impulse": leadership.get(
                    "confirmation_impulse"
                ),
                "quote_notional_leader": leadership.get("leader"),
                "leadership_reason": leadership.get("reason"),
                "router_applied": router.get("applied"),
                "entry": row.get("entry"),
                "stop": row.get("stop"),
                "target": row.get("target"),
                "net_r": row.get("net_r"),
            },
        )

    rejections = list(metrics.get("candidate_rejections", []))
    router_rejections = [
        row
        for row in rejections
        if row.get("type") == "AAC_EVENT_DIRECTION_LEADER_REJECTED"
    ]
    non_aac_rejections = [
        row
        for row in rejections
        if row.get("type") == "V48_NON_AAC_PLAN_REJECTED"
    ]
    rejection_reasons = Counter(
        str(row.get("reason", "UNKNOWN")) for row in router_rejections
    )
    rejected_ranks = Counter(
        str(row.get("event_direction_rank")) for row in router_rejections
    )
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics.update(
        {
            "week_start": args.week_start.isoformat(),
            "variant": args.variant,
            "candidate_generation": (
                "candidate-10-v48-independent-aac-event-leader"
            ),
            "v48_event_leader_only_enabled": (
                os.environ["C10_V48_AAC_EVENT_LEADER_ONLY"] == "1"
            ),
            "v48_accepted_event_rank_counts": dict(rank_counts),
            "v48_submitted_aac_plan_details": plan_details,
            "v48_event_router_rejection_count": len(router_rejections),
            "v48_event_router_rejection_reasons": dict(rejection_reasons),
            "v48_event_router_rejected_rank_counts": dict(rejected_ranks),
            "v48_non_aac_plan_rejection_count": len(non_aac_rejections),
            "v48_state_event_counts": {
                name: events.get(name, 0)
                for name in (
                    "LIQUIDITY_SWEEP",
                    "AAC_CONFIRMED",
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
