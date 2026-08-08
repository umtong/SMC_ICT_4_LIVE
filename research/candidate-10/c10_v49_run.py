#!/usr/bin/env python3
"""Run v49 rank-one FAR baseline versus transfer-state routing."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "event-leader-baseline",
    "event-transfer-state",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v49 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1"
    os.environ["C10_V40_SOURCE_EQUILIBRIUM_DETECTOR"] = "1"
    os.environ["C10_V36_CE_REJECTION"] = "0"
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1"
    os.environ["C10_V41_SOURCE_ENTRY_MODE"] = "FIRST_DISPLACEMENT_NEAR_EDGE"
    os.environ["C10_V44_PRIMARY_TARGET_MODE"] = "SOURCE_EQUILIBRIUM"
    os.environ["C10_V45_INVALIDATION_MODE"] = "SOURCE_RAID_EXTREME"
    os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_REFERENCE"] = "EXPECTED_ENTRY"
    os.environ["C10_V46_VOID_CLOSE_EXIT"] = "0"
    os.environ["C10_V47_EVENT_LEADER_ONLY"] = "1"
    os.environ["C10_V49_TRANSFER_STATE_ROUTER"] = (
        "1" if variant == "event-transfer-state" else "0"
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
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end-exclusive", type=date.fromisoformat, required=True)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--evidence-class", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.end_exclusive <= args.start:
        raise ValueError("end-exclusive must be after start")

    candidate_dir = args.candidate11_dir.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(candidate_dir))
    configure_variant(args.variant)

    evaluation_days = (args.end_exclusive - args.start).days
    config = json.loads(
        (candidate_dir / "config.json").read_text(encoding="utf-8"),
    )
    config["selection"]["weeks"]["V49"] = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end_exclusive.isoformat(),
    }
    config["selection"]["evaluation_days"] = evaluation_days
    config["v49_evaluation_contract"] = {
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "frozen_detector": "v40 decoupled source-range failed auction",
        "frozen_entry": "v41 first-displacement near-edge passive retrace",
        "frozen_primary_target": "source dealing-range equilibrium",
        "frozen_hard_stop_and_sizing": (
            "source raid extreme plus frozen ATR buffer and current-NAV 3% "
            "all-cost loss budget"
        ),
        "frozen_rank_router": (
            "candidate event-direction rank equals one among synchronized "
            "BTC/ETH/SOL/XRP observations"
        ),
        "only_alpha_variable": (
            "route rank-one FAR as distributed transfer or pioneer transfer "
            "before order submission"
        ),
        "distributed_transfer": (
            "peer event median positive; candidate confirmation impulse must "
            "meet the existing Candidate 11 follower threshold"
        ),
        "pioneer_transfer": (
            "peer event median non-positive; candidate trailing directional "
            "trend score must be negative"
        ),
        "new_fitted_thresholds": [],
        "risk_multiplier": "none",
        "post_fill_management": "hard source-raid stop only",
        "risk_fraction": 0.03,
        "continuous_account": evaluation_days > 7,
        "success_claim": False,
    }
    config_path = output / "v49_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V49", output)
    plans = load_rows(output / "submitted_plans.json", "plans")
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    life_counts = Counter(str(row.get("type", "UNKNOWN")) for row in lifecycle)

    state_counts: Counter[str] = Counter()
    accepted_details: list[dict[str, object]] = []
    for row in plans:
        details = row.get("details", {})
        if not isinstance(details, dict):
            continue
        leadership = details.get("market_leadership", {})
        transfer = details.get("event_transfer_state_router", {})
        if not isinstance(leadership, dict):
            leadership = {}
        if not isinstance(transfer, dict):
            transfer = {}
        state = str(transfer.get("state", "UNROUTED"))
        state_counts[state] += 1
        trends = leadership.get("directional_trend_scores", {})
        accepted_details.append(
            {
                "scenario_id": row.get("scenario_id"),
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "transfer_state": state,
                "event_direction_rank": leadership.get("event_direction_rank"),
                "candidate_event_move": leadership.get("candidate_event_move"),
                "peer_event_median": leadership.get("peer_event_median"),
                "confirmation_impulse": leadership.get("confirmation_impulse"),
                "candidate_trailing_directional_trend_score": (
                    trends.get(row.get("symbol")) if isinstance(trends, dict) else None
                ),
                "quote_notional_leader": leadership.get("leader"),
                "entry": row.get("entry"),
                "stop": row.get("stop"),
                "target": row.get("target"),
                "net_r": row.get("net_r"),
            },
        )

    transfer_rejections = [
        row
        for row in metrics.get("candidate_rejections", [])
        if row.get("type") == "EVENT_TRANSFER_STATE_REJECTED"
    ]
    rejection_reasons = Counter(
        str(row.get("reason", "UNKNOWN")) for row in transfer_rejections
    )
    rejection_states = Counter(
        str(row.get("transfer_state", "UNKNOWN")) for row in transfer_rejections
    )
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")

    metrics.update(
        {
            "evaluation_days": evaluation_days,
            "period_start": args.start.isoformat(),
            "period_end_exclusive": args.end_exclusive.isoformat(),
            "variant": args.variant,
            "evidence_class": args.evidence_class,
            "candidate_generation": "candidate-10-v49-cross-market-transfer-state-router",
            "v49_transfer_state_router_enabled": (
                os.environ["C10_V49_TRANSFER_STATE_ROUTER"] == "1"
            ),
            "v49_accepted_transfer_state_counts": dict(state_counts),
            "v49_accepted_transfer_details": accepted_details,
            "v49_transfer_rejection_count": len(transfer_rejections),
            "v49_transfer_rejection_reasons": dict(rejection_reasons),
            "v49_transfer_rejection_states": dict(rejection_states),
            "v49_pending_hard_stop_cancellation_count": life_counts.get(
                "PENDING_ENTRY_CANCELED_AFTER_INVALIDATION", 0
            ),
            "v49_pending_target_cancellation_count": life_counts.get(
                "PENDING_ENTRY_CANCELED_AFTER_TARGET_DELIVERY", 0
            ),
            "v49_state_event_counts": {
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
