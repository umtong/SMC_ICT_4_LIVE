#!/usr/bin/env python3
"""Run independent v63 flow-shock continuation continuously."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "all-complete-flow-plans",
    "event-leader-flow-plans",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v63 variant: {variant}")
    # Preserve the synchronized leadership measurements but ablate the original
    # AAC quote-notional-leader approval; v63 owns its own event-rank router.
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "1"
    os.environ["C10_V63_EVENT_LEADER_ONLY"] = (
        "1" if variant == "event-leader-flow-plans" else "0"
    )


def load_rows(path: Path, key: str) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get(key, [])
    return list(rows) if isinstance(rows, list) else []


def count_jsonl(path: Path, key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                counts[str(json.loads(line).get(key, "UNKNOWN"))] += 1
    return dict(counts)


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
    config["selection"]["weeks"]["V63"] = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end_exclusive.isoformat(),
    }
    config["selection"]["evaluation_days"] = evaluation_days
    config["v63_evaluation_contract"] = {
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "candidate11_source_commit": (
            "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327"
        ),
        "account_contract": (
            "one continuous NautilusTrader account with zero interval-internal "
            "NAV resets"
        ),
        "independent_scenario_family": (
            "completed 5-minute structure breakout with aggressor-flow "
            "reacceleration and pre-existing higher-timeframe liquidity target"
        ),
        "entry": "first displacement execution-void near-edge passive retrace",
        "invalidation": "last opposite confirmed 5-minute pivot plus frozen buffer",
        "target": "nearest still-live pre-existing 4-hour or UTC-day liquidity",
        "ablation": {
            "all-complete-flow-plans": "all structurally complete plans",
            "event-leader-flow-plans": (
                "candidate must rank first in the proposed synchronized "
                "sweep-to-confirmation direction"
            ),
        },
        "cost_and_risk": (
            "v27 size-dependent impact and current all-cost NAV times 3 percent "
            "maximum planned loss"
        ),
        "global_new_risk_slot": 1,
        "new_fitted_thresholds": [],
        "success_claim": False,
    }
    config_path = output / "v63_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V63", output)
    plans = load_rows(output / "submitted_plans.json", "plans")
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    accepted_details: list[dict[str, object]] = []
    ranks: Counter[str] = Counter()
    target_sources: Counter[str] = Counter()
    for row in plans:
        details = row.get("details", {})
        if not isinstance(details, dict):
            continue
        leadership = details.get("market_leadership", {})
        leadership = leadership if isinstance(leadership, dict) else {}
        rank = leadership.get("event_direction_rank")
        ranks[str(rank)] += 1
        source = str(details.get("target_pool_source", "UNKNOWN"))
        target_sources[source] += 1
        accepted_details.append({
            "scenario_id": row.get("scenario_id"),
            "symbol": row.get("symbol"),
            "direction": row.get("direction"),
            "observed_ts_ns": row.get("observed_ts_ns"),
            "event_direction_rank": rank,
            "trailing_direction_rank": leadership.get(
                "trailing_direction_rank"
            ),
            "candidate_event_move": leadership.get("candidate_event_move"),
            "peer_event_median": leadership.get("peer_event_median"),
            "target_pool_source": source,
            "breakout_level": details.get("breakout_level"),
            "entry": row.get("entry"),
            "stop": row.get("stop"),
            "target": row.get("target"),
            "net_r": row.get("net_r"),
        })

    rejections = [
        row for row in metrics.get("candidate_rejections", [])
        if row.get("type") == "FLOW_EVENT_LEADER_REJECTED"
    ]
    raw_counts = count_jsonl(
        output / "scenario_events.raw.jsonl",
        "event_type",
    )
    metrics.update({
        "evaluation_days": evaluation_days,
        "period_start": args.start.isoformat(),
        "period_end_exclusive": args.end_exclusive.isoformat(),
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "candidate_generation": (
            "candidate-10-v63-independent-flow-shock-continuation"
        ),
        "v63_continuous_account": True,
        "v63_nav_reset_count": 0,
        "v63_event_leader_enabled": (
            os.environ["C10_V63_EVENT_LEADER_ONLY"] == "1"
        ),
        "v63_accepted_event_rank_counts": dict(ranks),
        "v63_target_source_counts": dict(target_sources),
        "v63_accepted_details": accepted_details,
        "v63_router_rejection_count": len(rejections),
        "v63_router_rejection_reasons": dict(Counter(
            str(row.get("reason", "UNKNOWN")) for row in rejections
        )),
        "v63_lifecycle_counts": dict(Counter(
            str(row.get("type", "UNKNOWN")) for row in lifecycle
        )),
        "v63_state_event_counts": {
            name: raw_counts.get(name, 0)
            for name in (
                "FLOW_SHOCK_CONTINUATION_CONFIRMED",
                "TRADE_PLAN_CONFIRMED",
                "ENTRY_FILLED",
                "POSITION_TERMINAL",
                "EXTERNAL_LIQUIDITY_CONSUMED",
            )
        },
        "success_claim": False,
    })
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
