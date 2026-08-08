#!/usr/bin/env python3
"""Evaluate Candidate 24 retirement of the zero-fill pre-entry watch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import aggregate_candidate23_trend_resumption_far as base


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
    parser.add_argument("--candidate23", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    saved = sys.argv
    try:
        sys.argv = [
            "aggregate_candidate23_trend_resumption_far.py",
            "--root", str(args.root),
            "--baseline", str(args.baseline),
            "--candidate15", str(args.candidate15),
            "--candidate17", str(args.candidate17),
            "--candidate19", str(args.candidate19),
            "--candidate20", str(args.candidate20),
            "--candidate21", str(args.candidate21),
            "--candidate22", str(args.candidate22),
            "--output", str(args.output),
        ]
        status = base.main()
    finally:
        sys.argv = saved
    if status != 0:
        return status

    payload = json.loads(args.output.read_text(encoding="utf-8"))
    prior = json.loads(args.candidate23.read_text(encoding="utf-8"))
    payload["schema"] = "candidate-24-retired-semantic-rejection-watch-development-v1"
    payload["research_question"] = (
        "After the pre-entry rejected-FAR continuation state produced zero native "
        "fills across corrected Candidates 19-23, does terminating it immediately "
        "and returning the local detector to independent auction discovery restore "
        "valid opportunities without weakening any entry confirmation?"
    )
    payload["candidate24_overall"] = payload.pop("candidate23_overall")
    payload["candidate23_reference"] = {
        key: prior["candidate23_overall"][key]
        for key in (
            "daily_geometric_growth", "nav_multiple", "trades", "wins", "losses",
            "win_rate", "active_weeks", "payoff_ratio", "positive_growth_concentration",
        )
    }
    events = payload.get("semantic_rejected_far_event_counts", {})
    retired = int(events.get("SEMANTIC_REJECTED_FAR_WATCH_RETIRED", 0))
    armed = int(events.get("SEMANTIC_REJECTED_FAR_CONTINUATION_ARMED", 0))
    payload["allocator_contract"] = {
        "pre_entry_semantic_rejection": "terminal diagnosed event; no watch state",
        "retirement_evidence": (
            "Candidates 19-23: corrected inside-origin logic, costed acceptance "
            "plans and independent AAC gate yielded zero native fills"
        ),
        "immediate_inversion": False,
        "local_detector_release": True,
        "post_stop_failed_far_state": "unchanged",
        "ordinary_aac_far_and_trend_resumption": "unchanged",
        "numeric_threshold_change": None,
        "execution_target_cost_risk_change": None,
    }
    payload["retired_watch_count"] = retired
    payload["unexpected_armed_watch_count"] = armed

    current = payload["candidate24_overall"]
    reference = payload["candidate23_reference"]
    allocator_supported = (
        bool(current.get("all_safety_audits"))
        and retired > 0
        and armed == 0
        and float(current.get("daily_geometric_growth", 0.0))
        >= float(reference["daily_geometric_growth"])
        and int(current.get("trades", 0)) >= int(reference["trades"])
    )
    sufficient_opportunity = (
        int(current.get("trades", 0)) >= 12
        and int(current.get("active_weeks", 0)) >= 8
        and float(current.get("positive_growth_concentration", 1.0)) <= 0.60
    )
    payload["allocator_retirement_supported"] = allocator_supported
    payload["structural_recovery"] = allocator_supported and sufficient_opportunity
    if payload["structural_recovery"]:
        payload["decision"] = "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
    elif allocator_supported:
        payload["decision"] = "RETAIN_ALLOCATOR_RETIREMENT_REBUILD_OPPORTUNITY_ENGINE"
    else:
        payload["decision"] = "REJECT_OR_REDESIGN_ALLOCATOR_RETIREMENT"
    payload["success_claim"] = False
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Candidate 24 retired semantic-rejection watch development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Candidate 23 | {reference['daily_geometric_growth']:.6%} | {reference['nav_multiple']:.6f} | {reference['trades']} | {reference['wins']}/{reference['losses']} | {reference['win_rate']:.2%} | {reference['active_weeks']} | {fmt(reference['payoff_ratio'])} |",
        f"| Candidate 24 | {current['daily_geometric_growth']:.6%} | {current['nav_multiple']:.6f} | {current['trades']} | {current['wins']}/{current['losses']} | {current['win_rate']:.2%} | {current['active_weeks']} | {fmt(current.get('payoff_ratio'))} |",
        "",
        "## Allocator transition",
        "",
        f"- Retired rejected-FAR watches: {retired}",
        f"- Unexpected armed watches: {armed}",
        f"- Incremental trades vs C23: {int(current['trades']) - int(reference['trades'])}",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
