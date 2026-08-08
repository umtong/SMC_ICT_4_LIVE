#!/usr/bin/env python3
"""Relabel and evaluate Candidate 20 inside-origin acceptance evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import aggregate_candidate19_semantic_rejection_continuation as base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate15", type=Path, required=True)
    parser.add_argument("--candidate17", type=Path, required=True)
    parser.add_argument("--candidate19", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    saved = sys.argv
    try:
        sys.argv = [
            "aggregate_candidate19_semantic_rejection_continuation.py",
            "--root", str(args.root),
            "--baseline", str(args.baseline),
            "--candidate15", str(args.candidate15),
            "--candidate17", str(args.candidate17),
            "--output", str(args.output),
        ]
        status = base.main()
    finally:
        sys.argv = saved
    if status != 0:
        return status

    payload = json.loads(args.output.read_text(encoding="utf-8"))
    prior = json.loads(args.candidate19.read_text(encoding="utf-8"))
    payload["schema"] = "candidate-20-inside-origin-acceptance-development-v1"
    payload["research_question"] = (
        "When a complete FAR is rejected while price remains inside its swept "
        "boundary, can the watch wait neutrally for the first outside close and "
        "then require the full acceptance, defended-retest and synchronized "
        "reacceleration sequence without changing the post-stop semantics?"
    )
    payload["candidate20_overall"] = payload.pop("candidate19_overall")
    payload["candidate19_reference"] = {
        key: prior["candidate19_overall"][key]
        for key in (
            "daily_geometric_growth", "nav_multiple", "trades", "wins", "losses",
            "win_rate", "active_weeks", "payoff_ratio", "positive_growth_concentration",
        )
    }
    payload["origin_contract"] = {
        "post_stop": (
            "starts outside the failed sweep boundary; deep re-entry is immediately terminal"
        ),
        "semantic_rejection": (
            "starts inside after the completed reclaim; inside location is neutral until "
            "the first outside close"
        ),
        "after_first_outside_close": (
            "deep re-entry is terminal for both origins"
        ),
        "later_confirmation_and_execution": (
            "unchanged Candidate 17 acceptance, defended retest, reacceleration, "
            "AAC cross-market gate, strict target, costs and 3% NAV risk"
        ),
    }

    overall = payload["candidate20_overall"]
    semantic = payload["semantic_rejected_far_state"]
    recovery = (
        bool(overall.get("all_safety_audits"))
        and float(overall.get("daily_geometric_growth", 0.0)) > 0.0
        and semantic["trades"] >= 4
        and semantic["net_pnl"] > 0.0
        and semantic["win_rate"] >= 0.50
        and int(overall.get("active_weeks", 0)) >= int(payload["candidate19_reference"]["active_weeks"])
        and float(overall.get("positive_growth_concentration", 1.0)) <= 0.60
    )
    payload["structural_recovery"] = recovery
    payload["decision"] = (
        "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
        if recovery
        else "REJECT_OR_REDESIGN_INSIDE_ORIGIN_ACCEPTANCE_STATE"
    )
    payload["success_claim"] = False
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    current = payload["candidate20_overall"]
    previous = payload["candidate19_reference"]
    lines = [
        "# Candidate 20 inside-origin acceptance development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Candidate 19 | {previous['daily_geometric_growth']:.6%} | {previous['nav_multiple']:.6f} | {previous['trades']} | {previous['wins']}/{previous['losses']} | {previous['win_rate']:.2%} | {previous['active_weeks']} | {base.fmt(previous['payoff_ratio'])} |",
        f"| Candidate 20 | {current['daily_geometric_growth']:.6%} | {current['nav_multiple']:.6f} | {current['trades']} | {current['wins']}/{current['losses']} | {current['win_rate']:.2%} | {current['active_weeks']} | {base.fmt(current.get('payoff_ratio'))} |",
        "",
        "## Incremental semantic-rejected FAR state",
        "",
        f"- Armed watches: {payload['semantic_rejected_far_event_counts'].get('SEMANTIC_REJECTED_FAR_CONTINUATION_ARMED', 0)}",
        f"- Acceptance confirmations: {payload['semantic_rejected_far_event_counts'].get('FAILED_FAR_ACCEPTANCE_CONFIRMED', 0)}",
        f"- Defended retests: {payload['semantic_rejected_far_event_counts'].get('FAILED_FAR_BOUNDARY_RETEST_DEFENDED', 0)}",
        f"- Filled trades: {semantic['trades']}",
        f"- Wins/losses: {semantic['wins']}/{semantic['losses']}",
        f"- Net PnL: {semantic['net_pnl']:.2f} USDT",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
