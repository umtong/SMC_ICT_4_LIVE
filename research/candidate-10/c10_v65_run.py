#!/usr/bin/env python3
"""Run v65 breakout-resolution episodes continuously."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "all-resolved-episodes",
    "resolved-cross-market",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v65 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "1"
    os.environ["C10_V64_RESOLVED_ACCEPTANCE_ONLY"] = (
        "1" if variant == "resolved-cross-market" else "0"
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
    config["selection"]["weeks"]["V65"] = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end_exclusive.isoformat(),
    }
    config["selection"]["evaluation_days"] = evaluation_days
    config["v65_evaluation_contract"] = {
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "candidate11_source_commit": (
            "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327"
        ),
        "account_contract": (
            "one continuous NautilusTrader account with zero interval-internal "
            "NAV resets"
        ),
        "scenario": (
            "known five-minute flow breakout arms without entry; a later "
            "completed bar must either survive a broken-boundary retest or "
            "close back through the boundary with opposite aggressor flow"
        ),
        "resolution_routes": {
            "RETEST_CONTINUATION": (
                "same-direction accepted-auction continuation"
            ),
            "FAILED_ACCEPTANCE_REVERSAL": (
                "opposite-direction failed-breakout reversal"
            ),
            "UNRESOLVED": "no trade at the frozen retrace horizon",
        },
        "cross_market_interval": (
            "broken pivot known time through later resolution time"
        ),
        "entry_invalidation_target": (
            "passive broken-boundary entry, resolved-leg invalidation and "
            "nearest still-live confirmed five-minute liquidity objective"
        ),
        "position_management": (
            "target, structural stop, or one four-hour causal horizon expiry"
        ),
        "state_router": {
            "all-resolved-episodes": "every economically complete resolution",
            "resolved-cross-market": (
                "candidate and peer-median delivery, or event-leading candidate "
                "with independent completed-four-hour acceptance"
            ),
        },
        "cost_and_risk": (
            "size-dependent impact and current all-cost NAV times 3 percent "
            "maximum planned loss"
        ),
        "global_new_risk_slot": 1,
        "new_fitted_thresholds": [],
        "success_claim": False,
    }
    config_path = output / "v65_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V65", output)
    plans = load_rows(output / "submitted_plans.json", "plans")
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    accepted_details: list[dict[str, object]] = []
    resolutions: Counter[str] = Counter()
    scenarios: Counter[str] = Counter()
    states: Counter[str] = Counter()
    contexts: Counter[str] = Counter()
    ranks: Counter[str] = Counter()
    targets: Counter[str] = Counter()
    for row in plans:
        details = row.get("details", {})
        if not isinstance(details, dict):
            continue
        leadership = details.get("market_leadership", {})
        leadership = leadership if isinstance(leadership, dict) else {}
        router = details.get("intraday_acceptance_router", {})
        router = router if isinstance(router, dict) else {}
        context = details.get("completed_4h_context", {})
        context = context if isinstance(context, dict) else {}
        resolution = str(details.get("resolution", "UNKNOWN"))
        scenario = str(row.get("scenario", "UNKNOWN"))
        state = str(router.get("state", "ROUTER_DISABLED"))
        context_state = str(context.get("state", "UNKNOWN"))
        rank = leadership.get("event_direction_rank")
        target_source = str(details.get("target_pool_source", "UNKNOWN"))
        resolutions[resolution] += 1
        scenarios[scenario] += 1
        states[state] += 1
        contexts[context_state] += 1
        ranks[str(rank)] += 1
        targets[target_source] += 1
        accepted_details.append({
            "scenario_id": row.get("scenario_id"),
            "symbol": row.get("symbol"),
            "scenario": scenario,
            "direction": row.get("direction"),
            "observed_ts_ns": row.get("observed_ts_ns"),
            "breakout_episode_armed_ts_ns": details.get(
                "breakout_episode_armed_ts_ns"
            ),
            "breakout_pivot_known_ts_ns": details.get(
                "breakout_pivot_known_ts_ns"
            ),
            "resolution": resolution,
            "acceptance_state": state,
            "completed_4h_context_state": context_state,
            "event_direction_rank": rank,
            "candidate_event_move": leadership.get("candidate_event_move"),
            "peer_event_median": leadership.get("peer_event_median"),
            "event_path_efficiency": leadership.get("event_path_efficiency"),
            "event_standardized_displacement": leadership.get(
                "event_standardized_displacement"
            ),
            "breakout_direction": details.get("breakout_direction"),
            "trade_direction": details.get("trade_direction"),
            "acceptance_boundary": details.get("acceptance_boundary"),
            "invalidation_anchor": details.get("invalidation_anchor"),
            "target_pool_source": target_source,
            "entry": row.get("entry"),
            "stop": row.get("stop"),
            "target": row.get("target"),
            "position_expire_ts_ns": details.get("position_expire_ts_ns"),
            "net_r": row.get("net_r"),
        })

    rejections = [
        row for row in metrics.get("candidate_rejections", [])
        if row.get("type") == "INTRADAY_ACCEPTANCE_REJECTED"
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
            "candidate-10-v65-breakout-resolution-auction-router"
        ),
        "v65_continuous_account": True,
        "v65_nav_reset_count": 0,
        "v65_resolved_cross_market_enabled": (
            os.environ["C10_V64_RESOLVED_ACCEPTANCE_ONLY"] == "1"
        ),
        "v65_resolution_counts": dict(resolutions),
        "v65_scenario_counts": dict(scenarios),
        "v65_accepted_state_counts": dict(states),
        "v65_accepted_context_counts": dict(contexts),
        "v65_accepted_event_rank_counts": dict(ranks),
        "v65_target_source_counts": dict(targets),
        "v65_accepted_details": accepted_details,
        "v65_router_rejection_count": len(rejections),
        "v65_router_rejection_reasons": dict(Counter(
            str(row.get("reason", "UNKNOWN")) for row in rejections
        )),
        "v65_lifecycle_counts": dict(Counter(
            str(row.get("type", "UNKNOWN")) for row in lifecycle
        )),
        "v65_context_expiry_request_count": sum(
            str(row.get("type")) == "INTRADAY_CONTEXT_EXPIRY_REQUESTED"
            for row in lifecycle
        ),
        "v65_state_event_counts": {
            name: raw_counts.get(name, 0)
            for name in (
                "BREAKOUT_EPISODE_ARMED",
                "BREAKOUT_EPISODE_RESOLVED",
                "BREAKOUT_EPISODE_TERMINAL",
                "TRADE_PLAN_CONFIRMED",
                "ENTRY_FILLED",
                "POSITION_TERMINAL",
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
