#!/usr/bin/env python3
"""Run the Candidate 16 state-attribution engine for Candidate 17 evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import aggregate_candidate16_failed_far as base

NEW_KIND = "FAILED_FAR_STRICT_EXTERNAL_ACCEPTANCE_CONTINUATION"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate15", type=Path, required=True)
    parser.add_argument("--candidate16", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base.NEW_KIND = NEW_KIND
    original_argv = sys.argv
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
        sys.argv = original_argv
    if status != 0:
        return status

    payload = json.loads(args.output.read_text(encoding="utf-8"))
    prior = json.loads(args.candidate16.read_text(encoding="utf-8"))
    payload["schema"] = "candidate-17-strict-external-target-development-v1"
    payload["research_question"] = (
        "When a completed FAR is stopped and same-side acceptance forms, does "
        "requiring the nearest pre-existing unconsumed external pool strictly "
        "beyond both the failed boundary and stop-time price remove the already-"
        "consumed objective error while preserving an independent continuation edge?"
    )
    payload["candidate17_overall"] = payload.pop("candidate16_overall")
    payload["candidate16_reference"] = {
        key: prior["candidate16_overall"][key]
        for key in (
            "daily_geometric_growth",
            "nav_multiple",
            "trades",
            "wins",
            "losses",
            "win_rate",
            "active_weeks",
            "payoff_ratio",
            "positive_growth_concentration",
        )
    }
    payload["target_contract"] = {
        "causal_registry_only": True,
        "exclude_parent_scenario": True,
        "exclude_failed_boundary_duplicates": True,
        "require_unconsumed": True,
        "require_unexpired": True,
        "strictly_beyond_stop_reference_and_boundary": True,
        "selection": "nearest qualifying pool; LONG=min level, SHORT=max level",
        "fitted_distance_threshold": None,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    overall = payload["candidate17_overall"]
    state = payload["failed_far_state"]
    reference = payload["candidate16_reference"]
    payoff = overall.get("payoff_ratio")
    state_payoff = state.get("payoff_ratio")
    lines = [
        "# Candidate 17 strict external target development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| Candidate 16 | {reference['daily_geometric_growth']:.6%} | "
            f"{reference['nav_multiple']:.6f} | {reference['trades']} | "
            f"{reference['wins']}/{reference['losses']} | {reference['win_rate']:.2%} | "
            f"{reference['active_weeks']} | "
            f"{'n/a' if reference['payoff_ratio'] is None else f'{reference['payoff_ratio']:.3f}'} |"
        ),
        (
            f"| Candidate 17 | {overall['daily_geometric_growth']:.6%} | "
            f"{overall['nav_multiple']:.6f} | {overall['trades']} | "
            f"{overall['wins']}/{overall['losses']} | {overall['win_rate']:.2%} | "
            f"{overall['active_weeks']} | {'n/a' if payoff is None else f'{payoff:.3f}'} |"
        ),
        "",
        "## Incremental strict-target failed-FAR state",
        "",
        f"- Trades: {state['trades']}",
        f"- Wins/losses: {state['wins']}/{state['losses']}",
        f"- Win rate: {state['win_rate']:.2%}",
        f"- Net PnL: {state['net_pnl']:.2f} USDT",
        f"- Payoff: {'n/a' if state_payoff is None else f'{state_payoff:.3f}'}",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
