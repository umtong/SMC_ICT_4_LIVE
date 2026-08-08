#!/usr/bin/env python3
"""Attribute Candidate 30 quarter-hour failed-auction boundary-retest evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import aggregate_candidate29_quarter_hour_failed_auction as base

KIND = "QUARTER_HOUR_FAILED_AUCTION_BOUNDARY_RETEST"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fmt(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def reference(payload: dict[str, Any], key: str) -> dict[str, Any]:
    source = payload[key]
    fields = (
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
    return {name: source.get(name) for name in fields}


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
    parser.add_argument("--candidate25", type=Path, required=True)
    parser.add_argument("--candidate26", type=Path, required=True)
    parser.add_argument("--candidate27", type=Path, required=True)
    parser.add_argument("--candidate28", type=Path, required=True)
    parser.add_argument("--candidate29", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    saved = sys.argv
    try:
        sys.argv = [
            "aggregate_candidate29_quarter_hour_failed_auction.py",
            "--root", str(args.root),
            "--baseline", str(args.baseline),
            "--candidate15", str(args.candidate15),
            "--candidate17", str(args.candidate17),
            "--candidate19", str(args.candidate19),
            "--candidate20", str(args.candidate20),
            "--candidate21", str(args.candidate21),
            "--candidate22", str(args.candidate22),
            "--candidate25", str(args.candidate25),
            "--candidate26", str(args.candidate26),
            "--candidate27", str(args.candidate27),
            "--candidate28", str(args.candidate28),
            "--output", str(args.output),
        ]
        status = base.main()
    finally:
        sys.argv = saved
    if status != 0:
        return status

    payload = load(args.output)
    candidate29 = load(args.candidate29)

    payload["schema"] = (
        "candidate-30-quarter-hour-failed-auction-boundary-retest-development-v1"
    )
    payload["research_question"] = (
        "After a completed prior-quarter edge reclaim already shows opposite "
        "delivery, does waiting for a passive retest of that reclaimed boundary "
        "restore costed R and create a repeatable rotation to frozen prior-quarter "
        "equilibrium?"
    )
    payload["candidate30_overall"] = payload.pop("candidate29_overall")
    payload["candidate29_zero_fill_reference"] = {
        "decision": candidate29.get("decision"),
        **reference(candidate29, "candidate29_overall"),
        "incremental_state": {
            key: candidate29["quarter_hour_failed_auction_state"].get(key)
            for key in (
                "trades",
                "wins",
                "losses",
                "win_rate",
                "net_pnl",
                "payoff_ratio",
                "active_weeks",
            )
        },
        "plans_confirmed": candidate29[
            "quarter_hour_failed_auction_stage_counts"
        ].get("TRADE_PLAN_CONFIRMED"),
    }
    state = payload.pop("quarter_hour_failed_auction_state")
    payload["quarter_hour_boundary_retest_state"] = state
    payload["quarter_hour_boundary_retest_outcomes_by_direction"] = payload.pop(
        "quarter_hour_failed_auction_outcomes_by_direction"
    )
    payload["quarter_hour_boundary_retest_outcomes_by_symbol"] = payload.pop(
        "quarter_hour_failed_auction_outcomes_by_symbol"
    )
    payload["quarter_hour_boundary_retest_event_counts"] = payload.pop(
        "quarter_hour_failed_auction_event_counts"
    )
    payload["quarter_hour_boundary_retest_stage_counts"] = payload.pop(
        "quarter_hour_failed_auction_stage_counts"
    )
    payload["quarter_hour_boundary_retest_stage_conversions"] = payload.pop(
        "quarter_hour_failed_auction_stage_conversions"
    )
    payload["quarter_hour_boundary_retest_terminal_reasons"] = payload.pop(
        "quarter_hour_failed_auction_terminal_reasons"
    )
    payload["quarter_hour_boundary_retest_market_rejection_reasons"] = payload.pop(
        "quarter_hour_failed_auction_market_rejection_reasons"
    )
    payload["scenario_contract"] = {
        "detector": (
            "unchanged Candidate 29 first-minute trade-through of exactly one edge "
            "of the causally completed previous fifteen-minute range"
        ),
        "confirmation": (
            "a later completed bar closes back inside with opposite flow, body "
            "and close location using existing thresholds"
        ),
        "entry": (
            "post-only GTD limit at the reclaimed prior-range edge; Nautilus alone "
            "decides any later retest fill"
        ),
        "entry_expiry": "next UTC quarter-hour boundary",
        "target": "frozen midpoint of the completed previous quarter-hour range",
        "invalidation": "actual raid extreme plus existing ATR buffer",
        "portfolio_confirmation": "unchanged FAR synchronized-market semantic gate",
        "cost_gate": "unchanged after-cost minimum 1.25R",
        "cost_model": "maker entry, taker stop, maker target",
        "risk": "exact 3% current-NAV planned loss",
        "portfolio_slot": 1,
        "new_numeric_threshold": None,
    }

    current = payload["candidate30_overall"]
    reference25 = payload["candidate25_reference"]
    route_supported = (
        bool(current.get("all_safety_audits"))
        and state["trades"] >= 8
        and state["net_pnl"] > 0.0
        and state["active_weeks"] >= 4
        and float(current.get("daily_geometric_growth", 0.0))
        > float(reference25["daily_geometric_growth"])
        and float(current.get("nav_multiple", 0.0))
        > float(reference25["nav_multiple"])
        and float(current.get("positive_growth_concentration", 1.0)) <= 0.60
    )
    payload["quarter_hour_boundary_retest_route_supported"] = route_supported
    payload["structural_recovery"] = route_supported
    payload["decision"] = (
        "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
        if route_supported
        else "REJECT_OR_REDESIGN_QUARTER_HOUR_BOUNDARY_RETEST"
    )
    payload["success_claim"] = False

    # Candidate 29's row labels are causally identical; expose the new execution
    # name without altering recorded counts.
    for row in payload.get("weeks", []):
        row["quarter_hour_boundary_retest_sweeps"] = row.pop(
            "quarter_hour_failed_auction_sweeps",
            0,
        )
        row["quarter_hour_boundary_retest_reclaims"] = row.pop(
            "quarter_hour_failed_auction_reclaims",
            0,
        )
        row["quarter_hour_boundary_retest_plans"] = row.pop(
            "quarter_hour_failed_auction_plans",
            0,
        )
        row["quarter_hour_boundary_retest_filled_trades"] = row.pop(
            "quarter_hour_failed_auction_filled_trades",
            0,
        )

    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Candidate 30 quarter-hour failed-auction boundary retest",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| Candidate 25 | {reference25['daily_geometric_growth']:.6%} | "
            f"{reference25['nav_multiple']:.6f} | {reference25['trades']} | "
            f"{reference25['wins']}/{reference25['losses']} | "
            f"{reference25['win_rate']:.2%} | {reference25['active_weeks']} | "
            f"{fmt(reference25['payoff_ratio'])} |"
        ),
        (
            f"| Candidate 30 | {current['daily_geometric_growth']:.6%} | "
            f"{current['nav_multiple']:.6f} | {current['trades']} | "
            f"{current['wins']}/{current['losses']} | "
            f"{current['win_rate']:.2%} | {current['active_weeks']} | "
            f"{fmt(current.get('payoff_ratio'))} |"
        ),
        "",
        "## Incremental boundary-retest state",
        "",
        f"- Filled trades: {state['trades']}",
        f"- Wins/losses: {state['wins']}/{state['losses']}",
        f"- Win rate: {state['win_rate']:.2%}",
        f"- Net PnL: {state['net_pnl']:.2f} USDT",
        f"- Payoff: {fmt(state.get('payoff_ratio'))}",
        f"- Active weeks: {state['active_weeks']}",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
