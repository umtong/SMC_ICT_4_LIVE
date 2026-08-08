#!/usr/bin/env python3
"""Relabel and evaluate Candidate 21 two-close acceptance-entry evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import aggregate_candidate20_inside_origin_acceptance as base


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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    saved = sys.argv
    try:
        sys.argv = [
            "aggregate_candidate20_inside_origin_acceptance.py",
            "--root", str(args.root),
            "--baseline", str(args.baseline),
            "--candidate15", str(args.candidate15),
            "--candidate17", str(args.candidate17),
            "--candidate19", str(args.candidate19),
            "--output", str(args.output),
        ]
        status = base.main()
    finally:
        sys.argv = saved
    if status != 0:
        return status

    payload = json.loads(args.output.read_text(encoding="utf-8"))
    prior = json.loads(args.candidate20.read_text(encoding="utf-8"))
    payload["schema"] = "candidate-21-two-close-acceptance-entry-development-v1"
    payload["research_question"] = (
        "After a complete FAR is rejected by directional cross-market semantics, "
        "does invalidation of its reclaim through two completed closes beyond the "
        "swept boundary constitute the causal confirmation of a new continuation "
        "auction, without requiring a second boundary retest and reacceleration?"
    )
    payload["candidate21_overall"] = payload.pop("candidate20_overall")
    payload["candidate20_reference"] = {
        key: prior["candidate20_overall"][key]
        for key in (
            "daily_geometric_growth", "nav_multiple", "trades", "wins", "losses",
            "win_rate", "active_weeks", "payoff_ratio", "positive_growth_concentration",
        )
    }
    payload["entry_contract"] = {
        "eligible_origin": "SEMANTIC_REJECTION only",
        "confirmation": (
            "two completed closes beyond the original swept boundary after the "
            "complete local FAR was rejected before entry"
        ),
        "portfolio_approval": "unchanged AAC cross-market semantic gate",
        "entry": "native market order after the completed second outside close",
        "invalidation": "failed boundary reacceptance plus existing ATR buffer",
        "target": "nearest strict pre-existing unconsumed external pool",
        "cost_gate": "unchanged after-cost minimum 1.25R",
        "risk": "exact 3% current-NAV planned loss",
        "portfolio_slot": 1,
        "post_stop_path": (
            "unchanged acceptance -> defended boundary retest -> reacceleration"
        ),
    }

    overall = payload["candidate21_overall"]
    previous = payload["candidate20_reference"]
    semantic = payload["semantic_rejected_far_state"]
    recovery = (
        bool(overall.get("all_safety_audits"))
        and float(overall.get("daily_geometric_growth", 0.0))
        > float(previous["daily_geometric_growth"])
        and semantic["trades"] >= 4
        and semantic["net_pnl"] > 0.0
        and semantic["win_rate"] >= 0.50
        and int(overall.get("active_weeks", 0)) >= int(previous["active_weeks"])
        and float(overall.get("positive_growth_concentration", 1.0)) <= 0.60
    )
    payload["structural_recovery"] = recovery
    payload["decision"] = (
        "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
        if recovery
        else "REJECT_OR_REDESIGN_TWO_CLOSE_ACCEPTANCE_ENTRY"
    )
    payload["success_claim"] = False
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    current = payload["candidate21_overall"]
    lines = [
        "# Candidate 21 two-close acceptance-entry development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Candidate 20 | {previous['daily_geometric_growth']:.6%} | {previous['nav_multiple']:.6f} | {previous['trades']} | {previous['wins']}/{previous['losses']} | {previous['win_rate']:.2%} | {previous['active_weeks']} | {fmt(previous['payoff_ratio'])} |",
        f"| Candidate 21 | {current['daily_geometric_growth']:.6%} | {current['nav_multiple']:.6f} | {current['trades']} | {current['wins']}/{current['losses']} | {current['win_rate']:.2%} | {current['active_weeks']} | {fmt(current.get('payoff_ratio'))} |",
        "",
        "## Incremental semantic-rejected FAR state",
        "",
        f"- Armed watches: {payload['semantic_rejected_far_event_counts'].get('SEMANTIC_REJECTED_FAR_CONTINUATION_ARMED', 0)}",
        f"- Acceptance confirmations: {payload['semantic_rejected_far_event_counts'].get('FAILED_FAR_ACCEPTANCE_CONFIRMED', 0)}",
        f"- Plans confirmed: {payload['semantic_rejected_far_event_counts'].get('TRADE_PLAN_CONFIRMED', 0)}",
        f"- Filled trades: {semantic['trades']}",
        f"- Wins/losses: {semantic['wins']}/{semantic['losses']}",
        f"- Win rate: {semantic['win_rate']:.2%}",
        f"- Net PnL: {semantic['net_pnl']:.2f} USDT",
        f"- Payoff: {fmt(semantic.get('payoff_ratio'))}",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
