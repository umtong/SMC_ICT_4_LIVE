#!/usr/bin/env python3
"""Evaluate Candidate 25: C22 route policy plus retired zero-fill watch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import aggregate_candidate22_contested_countertrend as base


def fmt(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate15", type=Path, required=True)
    parser.add_argument("--candidate17", type=Path, required=True)
    parser.add_argument("--candidate19", type=Path, required=True)
    parser.add_argument("--candidate20", type=Path, required=True)
    parser.add_argument("--candidate21", type=Path, required=True)
    parser.add_argument("--candidate22", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    saved = sys.argv
    try:
        sys.argv = [
            "aggregate_candidate22_contested_countertrend.py",
            "--root", str(args.root),
            "--baseline", str(args.baseline),
            "--candidate15", str(args.candidate15),
            "--candidate17", str(args.candidate17),
            "--candidate19", str(args.candidate19),
            "--candidate20", str(args.candidate20),
            "--candidate21", str(args.candidate21),
            "--output", str(args.output),
        ]
        status = base.main()
    finally:
        sys.argv = saved
    if status != 0:
        return status

    payload = json.loads(args.output.read_text(encoding="utf-8"))
    prior = json.loads(args.candidate22.read_text(encoding="utf-8"))
    payload["schema"] = "candidate-25-clean-allocator-development-v1"
    payload["research_question"] = (
        "After removing the negative moderate-countertrend route and rejecting the "
        "negative trend-resumption role, does retiring the zero-fill pre-entry "
        "semantic watch expose additional independent AAC or dominant-peer FAR "
        "opportunities without changing any confirmation or execution rule?"
    )
    payload["candidate25_overall"] = payload.pop("candidate22_overall")
    payload["candidate22_reference"] = {
        key: prior["candidate22_overall"][key]
        for key in (
            "daily_geometric_growth", "nav_multiple", "trades", "wins", "losses",
            "win_rate", "active_weeks", "payoff_ratio", "positive_growth_concentration",
        )
    }
    events = payload.get("semantic_rejected_far_event_counts", {})
    retired = int(events.get("SEMANTIC_REJECTED_FAR_WATCH_RETIRED", 0))
    armed = int(events.get("SEMANTIC_REJECTED_FAR_CONTINUATION_ARMED", 0))
    routes = payload.get("native_outcomes_by_semantic_route", {})
    payload["clean_allocator_contract"] = {
        "retained_immediate_routes": [
            "SEMANTIC_AAC_ALIGNED_SYNCHRONIZED_NONLAGGARD",
            "SEMANTIC_FAR_DOMINANT_PEER_QUORUM",
        ],
        "moderate_countertrend": "contested and rejected before entry",
        "trend_resumption_far": "not installed; Candidate 23 role rejected",
        "pre_entry_rejected_far_watch": "retired terminal diagnostic",
        "post_stop_failed_far": "unchanged",
        "numeric_threshold_change": None,
        "execution_target_cost_risk_change": None,
    }
    payload["retired_watch_count"] = retired
    payload["unexpected_armed_watch_count"] = armed
    payload["unexpected_trend_resumption_fills"] = int(
        routes.get("SEMANTIC_FAR_TREND_RESUMPTION_SYNCHRONIZED", {}).get("trades", 0)
    )

    current = payload["candidate25_overall"]
    reference = payload["candidate22_reference"]
    allocator_increment = int(current.get("trades", 0)) - int(reference["trades"])
    supported = (
        bool(current.get("all_safety_audits"))
        and retired > 0
        and armed == 0
        and payload["unexpected_trend_resumption_fills"] == 0
        and float(current.get("daily_geometric_growth", 0.0))
        >= float(reference["daily_geometric_growth"])
        and allocator_increment >= 0
    )
    payload["clean_allocator_supported"] = supported
    payload["structural_recovery"] = (
        supported
        and int(current.get("trades", 0)) >= 12
        and int(current.get("active_weeks", 0)) >= 8
        and float(current.get("positive_growth_concentration", 1.0)) <= 0.60
    )
    if payload["structural_recovery"]:
        payload["decision"] = "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
    elif supported:
        payload["decision"] = "RETAIN_CLEAN_ALLOCATOR_BUILD_NEW_INTERNAL_LIQUIDITY_SCENARIO"
    else:
        payload["decision"] = "REJECT_CLEAN_ALLOCATOR_ABLATION"
    payload["success_claim"] = False
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Candidate 25 clean allocator development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Candidate 22 | {reference['daily_geometric_growth']:.6%} | {reference['nav_multiple']:.6f} | {reference['trades']} | {reference['wins']}/{reference['losses']} | {reference['win_rate']:.2%} | {reference['active_weeks']} | {fmt(reference['payoff_ratio'])} |",
        f"| Candidate 25 | {current['daily_geometric_growth']:.6%} | {current['nav_multiple']:.6f} | {current['trades']} | {current['wins']}/{current['losses']} | {current['win_rate']:.2%} | {current['active_weeks']} | {fmt(current.get('payoff_ratio'))} |",
        "",
        "## Allocator ablation",
        "",
        f"- Retired watches: {retired}",
        f"- Unexpected armed watches: {armed}",
        f"- Incremental trades vs C22: {allocator_increment}",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
