#!/usr/bin/env python3
"""Run v47 FAR contracts on one continuous, non-resetting account interval.

This is not a weekly-multiple study.  One NautilusTrader engine owns orders,
fills, fees, margin, positions and NAV for the entire requested interval.  The
only comparison is the frozen v47 market-state router:

* all plans approved by the frozen Candidate 11 leadership gate; or
* the candidate must be event-direction rank one at confirmation.

Entry, target, hard stop, all-cost sizing, market impact and the global portfolio
slot are identical.  No new alpha threshold is introduced.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "all-approved-hard-stop",
    "event-leader-hard-stop",
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
    os.environ["C10_V47_EVENT_LEADER_ONLY"] = (
        "1" if variant == "event-leader-hard-stop" else "0"
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
        "candidate11_source_commit": (
            "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327"
        ),
        "account_contract": (
            "one continuous NautilusTrader account; NAV is never reset inside "
            "the interval and weekly return multiplication is prohibited"
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
        "only_alpha_variable": (
            "all frozen leadership-approved FAR plans versus candidate "
            "event-direction rank equal to one"
        ),
        "post_fill_management": "hard source-raid stop only",
        "global_new_risk_slot": 1,
        "new_fitted_thresholds": [],
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
    rank_counts: Counter[str] = Counter()
    trailing_rank_counts: Counter[str] = Counter()
    router_details: list[dict[str, object]] = []
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
        event_rank = leadership.get("event_direction_rank")
        trailing_rank = leadership.get("trailing_direction_rank")
        rank_counts[str(event_rank)] += 1
        trailing_rank_counts[str(trailing_rank)] += 1
        router_details.append(
            {
                "scenario_id": row.get("scenario_id"),
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "observed_ts_ns": row.get("observed_ts_ns"),
                "event_direction_rank": event_rank,
                "trailing_direction_rank": trailing_rank,
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
    life_counts = Counter(str(row.get("type", "UNKNOWN")) for row in lifecycle)
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics.update(
        {
            "interval_start": args.start.isoformat(),
            "interval_end_exclusive": args.end_exclusive.isoformat(),
            "variant": args.variant,
            "candidate_generation": (
                "candidate-10-v49-continuous-2026-far-validation"
            ),
            "v49_continuous_account": True,
            "v49_nav_reset_count": 0,
            "v49_event_leader_only_enabled": (
                os.environ["C10_V47_EVENT_LEADER_ONLY"] == "1"
            ),
            "v49_accepted_event_rank_counts": dict(rank_counts),
            "v49_accepted_trailing_rank_counts": dict(trailing_rank_counts),
            "v49_accepted_router_details": router_details,
            "v49_event_router_rejection_count": len(router_rejections),
            "v49_event_router_rejection_reasons": dict(
                Counter(
                    str(row.get("reason", "UNKNOWN"))
                    for row in router_rejections
                ),
            ),
            "v49_lifecycle_counts": dict(life_counts),
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
