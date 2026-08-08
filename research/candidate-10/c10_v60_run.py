#!/usr/bin/env python3
"""Run v60 event-leader versus endpoint extreme-state FAR continuously."""
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
    "event-leader-extreme-state",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v60 variant: {variant}")
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
    os.environ["C10_V60_EXTREME_STATE_ROUTER"] = (
        "1" if variant == "event-leader-extreme-state" else "0"
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
    config["selection"]["weeks"]["V60"] = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end_exclusive.isoformat(),
    }
    config["selection"]["evaluation_days"] = evaluation_days
    config["v60_evaluation_contract"] = {
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "candidate11_source_commit": (
            "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327"
        ),
        "account_contract": (
            "one continuous NautilusTrader account; no interval-internal NAV "
            "reset and no multiplication of reset-period returns"
        ),
        "evaluation_start": args.start.isoformat(),
        "evaluation_end_exclusive": args.end_exclusive.isoformat(),
        "evaluation_calendar_days": evaluation_days,
        "frozen_detector": "v40 decoupled source-range failed auction",
        "frozen_entry": "v41 first-displacement near-edge passive retrace",
        "frozen_primary_target": "source dealing-range equilibrium",
        "frozen_hard_stop_and_sizing": (
            "source raid extreme plus frozen ATR buffer and current all-cost "
            "NAV times 3 percent maximum planned loss"
        ),
        "frozen_first_router": "candidate event-direction rank equals one",
        "only_alpha_variable": {
            "event-leader-baseline": (
                "all v47 event-direction rank-one candidates"
            ),
            "event-leader-extreme-state": (
                "rank-one candidates only when trailing direction rank is "
                "either one or synchronized market count"
            ),
        },
        "extreme_states": {
            "RELATIVE_STRENGTH_RESILIENCE": (
                "trailing rank one to event rank one"
            ),
            "CROSS_SECTIONAL_RANK_REVERSAL": (
                "trailing last rank to event rank one"
            ),
            "UNRESOLVED": "interior trailing ranks",
        },
        "post_fill_management": "hard source-raid stop only",
        "global_new_risk_slot": 1,
        "new_fitted_thresholds": [],
        "risk_multiplier": "none",
        "success_claim": False,
    }
    config_path = output / "v60_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V60", output)
    plans = load_rows(output / "submitted_plans.json", "plans")
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    accepted_states: Counter[str] = Counter()
    accepted_rank_pairs: Counter[str] = Counter()
    accepted_details: list[dict[str, object]] = []
    for row in plans:
        details = row.get("details", {})
        if not isinstance(details, dict):
            continue
        leadership = details.get("market_leadership", {})
        state = details.get("cross_sectional_extreme_state_router", {})
        if not isinstance(leadership, dict):
            leadership = {}
        if not isinstance(state, dict):
            state = {}
        selected = str(state.get("selected_state", "MISSING"))
        trailing_rank = leadership.get("trailing_direction_rank")
        event_rank = leadership.get("event_direction_rank")
        accepted_states[selected] += 1
        accepted_rank_pairs[f"{trailing_rank}->{event_rank}"] += 1
        accepted_details.append(
            {
                "scenario_id": row.get("scenario_id"),
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "observed_ts_ns": row.get("observed_ts_ns"),
                "selected_state": selected,
                "trailing_direction_rank": trailing_rank,
                "event_direction_rank": event_rank,
                "candidate_event_move": leadership.get("candidate_event_move"),
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
                "entry": row.get("entry"),
                "stop": row.get("stop"),
                "target": row.get("target"),
                "net_r": row.get("net_r"),
            },
        )

    rejections = [
        row
        for row in metrics.get("candidate_rejections", [])
        if row.get("type") == "CROSS_SECTIONAL_EXTREME_STATE_REJECTED"
    ]
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    life_counts = Counter(str(row.get("type", "UNKNOWN")) for row in lifecycle)
    metrics.update(
        {
            "evaluation_days": evaluation_days,
            "period_start": args.start.isoformat(),
            "period_end_exclusive": args.end_exclusive.isoformat(),
            "variant": args.variant,
            "evidence_class": args.evidence_class,
            "candidate_generation": (
                "candidate-10-v60-cross-sectional-extreme-state-router"
            ),
            "v60_continuous_account": True,
            "v60_nav_reset_count": 0,
            "v60_extreme_state_router_enabled": (
                os.environ["C10_V60_EXTREME_STATE_ROUTER"] == "1"
            ),
            "v60_accepted_state_counts": dict(accepted_states),
            "v60_accepted_rank_pair_counts": dict(accepted_rank_pairs),
            "v60_accepted_details": accepted_details,
            "v60_rejection_count": len(rejections),
            "v60_rejection_reasons": dict(
                Counter(
                    str(row.get("reason", "UNKNOWN")) for row in rejections
                ),
            ),
            "v60_rejected_trailing_rank_counts": dict(
                Counter(
                    str(row.get("trailing_direction_rank"))
                    for row in rejections
                ),
            ),
            "v60_lifecycle_counts": dict(life_counts),
            "v60_state_event_counts": {
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
