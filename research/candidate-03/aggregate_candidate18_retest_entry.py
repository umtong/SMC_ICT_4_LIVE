#!/usr/bin/env python3
"""Attribute Candidate 18 completed-retest entry development evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import aggregate_candidate16_failed_far as base

NEW_KIND = "FAILED_FAR_STRICT_TARGET_RETEST_CONTINUATION"


def fmt(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate15", type=Path, required=True)
    parser.add_argument("--candidate17", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base.NEW_KIND = NEW_KIND
    saved = sys.argv
    try:
        sys.argv = [
            "aggregate_candidate16_failed_far.py",
            "--root", str(args.root),
            "--baseline", str(args.baseline),
            "--candidate15", str(args.candidate15),
            "--output", str(args.output),
        ]
        status = base.main()
    finally:
        sys.argv = saved
    if status != 0:
        return status

    payload = json.loads(args.output.read_text(encoding="utf-8"))
    prior = json.loads(args.candidate17.read_text(encoding="utf-8"))
    payload["schema"] = "candidate-18-completed-retest-entry-development-v1"
    payload["research_question"] = (
        "After a real FAR stop and two-close acceptance beyond the failed sweep, "
        "is a completed failed-boundary retest closing outside sufficient causal "
        "entry confirmation, making a later reacceleration break redundant?"
    )
    payload["candidate18_overall"] = payload.pop("candidate16_overall")
    payload["candidate17_reference"] = {
        key: prior["candidate17_overall"][key]
        for key in (
            "daily_geometric_growth", "nav_multiple", "trades", "wins", "losses",
            "win_rate", "active_weeks", "payoff_ratio", "positive_growth_concentration",
        )
    }
    payload["entry_contract"] = {
        "first_confirmation": "two completed closes beyond failed sweep boundary",
        "second_confirmation": "completed boundary retest touching the zone and closing outside",
        "removed_duplicate_confirmation": "later break through acceptance impulse extreme",
        "entry": "next native quote market order after retest close",
        "stop": "retest extreme and failed boundary plus frozen ATR buffer",
        "target": "Candidate 17 strict pre-existing external pool",
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    current = payload["candidate18_overall"]
    previous = payload["candidate17_reference"]
    state = payload["failed_far_state"]
    lines = [
        "# Candidate 18 completed-retest entry development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Candidate 17 | {previous['daily_geometric_growth']:.6%} | {previous['nav_multiple']:.6f} | {previous['trades']} | {previous['wins']}/{previous['losses']} | {previous['win_rate']:.2%} | {previous['active_weeks']} | {fmt(previous['payoff_ratio'])} |",
        f"| Candidate 18 | {current['daily_geometric_growth']:.6%} | {current['nav_multiple']:.6f} | {current['trades']} | {current['wins']}/{current['losses']} | {current['win_rate']:.2%} | {current['active_weeks']} | {fmt(current.get('payoff_ratio'))} |",
        "",
        "## Incremental completed-retest state",
        "",
        f"- Trades: {state['trades']}",
        f"- Wins/losses: {state['wins']}/{state['losses']}",
        f"- Win rate: {state['win_rate']:.2%}",
        f"- Net PnL: {state['net_pnl']:.2f} USDT",
        f"- Payoff: {fmt(state.get('payoff_ratio'))}",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
