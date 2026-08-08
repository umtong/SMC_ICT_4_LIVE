#!/usr/bin/env python3
"""Evaluate Candidate 23 ordinary trend-resumption FAR as a distinct role."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import aggregate_candidate22_contested_countertrend as base

ROUTE = "SEMANTIC_FAR_TREND_RESUMPTION_SYNCHRONIZED"


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
    payload["schema"] = "candidate-23-trend-resumption-far-development-v1"
    payload["research_question"] = (
        "When a pullback raids liquidity against an already aligned candidate and "
        "market-wide trailing auction, does a complete local reclaim with unanimous "
        "peer response, top-half event price discovery and the existing efficient-"
        "displacement requirements form a distinct trend-resumption FAR edge?"
    )
    payload["candidate23_overall"] = payload.pop("candidate22_overall")
    payload["candidate22_reference"] = {
        key: prior["candidate22_overall"][key]
        for key in (
            "daily_geometric_growth", "nav_multiple", "trades", "wins", "losses",
            "win_rate", "active_weeks", "payoff_ratio", "positive_growth_concentration",
        )
    }
    routes = payload["native_outcomes_by_semantic_route"]
    trend = routes.get(
        ROUTE,
        {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "active_weeks": [],
        },
    )
    payload["trend_resumption_far_state"] = trend
    payload["trend_resumption_contract"] = {
        "local_pattern": "complete FAR reclaim after a liquidity raid",
        "candidate_trailing_auction": "aligned with proposed direction",
        "market_median_trailing_auction": "aligned with proposed direction",
        "event_peer_requirement": "unanimous direction",
        "price_discovery": "top half of four synchronized markets",
        "path_efficiency": "unchanged existing minimum",
        "standardized_displacement": "unchanged existing minimum",
        "new_numeric_threshold": None,
        "entry_target_cost_risk": "unchanged Candidate 22 runtime",
        "countertrend_role": "separate contested or dominant-quorum state",
    }

    current = payload["candidate23_overall"]
    reference = payload["candidate22_reference"]
    trend_supported = (
        int(trend["trades"]) >= 3
        and float(trend["net_pnl"]) > 0.0
        and float(trend["win_rate"]) >= 0.50
        and len(trend.get("active_weeks", [])) >= 3
    )
    aggregate_improved = (
        bool(current.get("all_safety_audits"))
        and float(current.get("daily_geometric_growth", 0.0))
        > float(reference["daily_geometric_growth"])
    )
    sufficient_opportunity = (
        int(current.get("trades", 0)) >= 12
        and int(current.get("active_weeks", 0)) >= 8
        and float(current.get("positive_growth_concentration", 1.0)) <= 0.60
    )
    payload["trend_resumption_role_supported"] = trend_supported
    payload["structural_recovery"] = (
        trend_supported and aggregate_improved and sufficient_opportunity
    )
    if payload["structural_recovery"]:
        payload["decision"] = "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
    elif trend_supported and aggregate_improved:
        payload["decision"] = "RETAIN_TREND_RESUMPTION_REBUILD_OPPORTUNITY_ENGINE"
    else:
        payload["decision"] = "REJECT_OR_REDESIGN_TREND_RESUMPTION_FAR_ROLE"
    payload["success_claim"] = False
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Candidate 23 trend-resumption FAR development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Candidate 22 | {reference['daily_geometric_growth']:.6%} | {reference['nav_multiple']:.6f} | {reference['trades']} | {reference['wins']}/{reference['losses']} | {reference['win_rate']:.2%} | {reference['active_weeks']} | {fmt(reference['payoff_ratio'])} |",
        f"| Candidate 23 | {current['daily_geometric_growth']:.6%} | {current['nav_multiple']:.6f} | {current['trades']} | {current['wins']}/{current['losses']} | {current['win_rate']:.2%} | {current['active_weeks']} | {fmt(current.get('payoff_ratio'))} |",
        "",
        "## Incremental trend-resumption FAR role",
        "",
        f"- Trades: {trend['trades']}",
        f"- Wins/losses: {trend['wins']}/{trend['losses']}",
        f"- Win rate: {float(trend['win_rate']):.2%}",
        f"- Net PnL: {float(trend['net_pnl']):.2f} USDT",
        f"- Active weeks: {', '.join(trend.get('active_weeks', [])) or 'none'}",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
