#!/usr/bin/env python3
"""Evaluate Candidate 22 contested-countertrend route reclassification."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import re
import sys

import aggregate_candidate21_two_close_acceptance_entry as base

WEEKS = tuple(f"H{i:02d}" for i in range(1, 17))
OLD_ROUTE = "SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS"
NEW_ROUTE = "SEMANTIC_FAR_MODERATE_COUNTERTREND_CONTESTED"


def fmt(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def pnl_value(raw: str) -> float:
    match = re.search(r"-?[0-9]+(?:\.[0-9]+)?", str(raw))
    if match is None:
        raise ValueError(f"cannot parse realized PnL: {raw!r}")
    return float(match.group())


def route_attribution(root: Path) -> dict[str, dict[str, object]]:
    outcomes: dict[str, dict[str, object]] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0, "weeks": set()}
    )
    for week in WEEKS:
        folder = root / week
        plans = json.loads((folder / "submitted_plans.json").read_text(encoding="utf-8"))["plans"]
        used: set[int] = set()
        with (folder / "positions.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                open_ns = int(row["ts_init"])
                symbol = str(row["instrument_id"]).split("-")[0]
                eligible = [
                    (index, plan)
                    for index, plan in enumerate(plans)
                    if index not in used
                    and plan["symbol"] == symbol
                    and int(plan["observed_ts_ns"]) <= open_ns
                ]
                if not eligible:
                    raise RuntimeError(
                        f"cannot map native position to submitted plan: {week} {symbol} {open_ns}"
                    )
                index, plan = max(eligible, key=lambda item: int(item[1]["observed_ts_ns"]))
                used.add(index)
                reason = str(
                    plan.get("details", {})
                    .get("market_leadership", {})
                    .get("reason", "NO_MARKET_LEADERSHIP_REASON")
                )
                pnl = pnl_value(row["realized_pnl"])
                bucket = outcomes[reason]
                bucket["trades"] = int(bucket["trades"]) + 1
                bucket["wins"] = int(bucket["wins"]) + int(pnl > 0.0)
                bucket["losses"] = int(bucket["losses"]) + int(pnl < 0.0)
                bucket["net_pnl"] = float(bucket["net_pnl"]) + pnl
                bucket["weeks"].add(week)
    result: dict[str, dict[str, object]] = {}
    for reason, values in outcomes.items():
        result[reason] = {
            "trades": values["trades"],
            "wins": values["wins"],
            "losses": values["losses"],
            "win_rate": (
                float(values["wins"]) / int(values["trades"])
                if int(values["trades"]) else 0.0
            ),
            "net_pnl": round(float(values["net_pnl"]), 8),
            "active_weeks": sorted(values["weeks"]),
        }
    return dict(sorted(result.items()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate15", type=Path, required=True)
    parser.add_argument("--candidate17", type=Path, required=True)
    parser.add_argument("--candidate19", type=Path, required=True)
    parser.add_argument("--candidate20", type=Path, required=True)
    parser.add_argument("--candidate21", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    saved = sys.argv
    try:
        sys.argv = [
            "aggregate_candidate21_two_close_acceptance_entry.py",
            "--root", str(args.root),
            "--baseline", str(args.baseline),
            "--candidate15", str(args.candidate15),
            "--candidate17", str(args.candidate17),
            "--candidate19", str(args.candidate19),
            "--candidate20", str(args.candidate20),
            "--output", str(args.output),
        ]
        status = base.main()
    finally:
        sys.argv = saved
    if status != 0:
        return status

    payload = json.loads(args.output.read_text(encoding="utf-8"))
    prior = json.loads(args.candidate21.read_text(encoding="utf-8"))
    payload["schema"] = "candidate-22-contested-countertrend-resolution-development-v1"
    payload["research_question"] = (
        "When short-horizon peer reclaim is unanimous but both the candidate and "
        "market-wide trailing auctions remain adverse, does treating ordinary FAR "
        "as a contested auction rather than an immediately tradable reversal remove "
        "the structurally negative route while preserving later causal resolution?"
    )
    payload["candidate22_overall"] = payload.pop("candidate21_overall")
    payload["candidate21_reference"] = {
        key: prior["candidate21_overall"][key]
        for key in (
            "daily_geometric_growth", "nav_multiple", "trades", "wins", "losses",
            "win_rate", "active_weeks", "payoff_ratio", "positive_growth_concentration",
        )
    }
    routes = route_attribution(args.root)
    payload["native_outcomes_by_semantic_route"] = routes
    payload["reclassification_contract"] = {
        "old_immediate_route": OLD_ROUTE,
        "new_contested_reason": NEW_ROUTE,
        "new_immediate_order": False,
        "resolution": (
            "existing semantic-rejection watch; only later two-close acceptance may "
            "create a continuation plan"
        ),
        "unchanged_stronger_far_route": "SEMANTIC_FAR_DOMINANT_PEER_QUORUM",
        "unchanged_session_policy": "completed I7 route-specific substitution",
        "new_numeric_threshold": None,
        "risk_change": None,
    }

    overall = payload["candidate22_overall"]
    previous = payload["candidate21_reference"]
    old_fills = int(routes.get(OLD_ROUTE, {}).get("trades", 0))
    contested_armed = int(
        payload["semantic_rejected_far_seed_reasons"].get(NEW_ROUTE, 0)
    )
    quality_improved = (
        bool(overall.get("all_safety_audits"))
        and float(overall.get("daily_geometric_growth", 0.0))
        > float(previous["daily_geometric_growth"])
        and old_fills == 0
        and contested_armed > 0
    )
    sufficient_opportunity = (
        int(overall.get("trades", 0)) >= 12
        and int(overall.get("active_weeks", 0)) >= 8
        and float(overall.get("positive_growth_concentration", 1.0)) <= 0.60
    )
    payload["route_reclassification_supported"] = quality_improved
    payload["structural_recovery"] = quality_improved and sufficient_opportunity
    if payload["structural_recovery"]:
        payload["decision"] = "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
    elif quality_improved:
        payload["decision"] = "RETAIN_RECLASSIFICATION_REBUILD_OPPORTUNITY_ENGINE"
    else:
        payload["decision"] = "REJECT_CONTESTED_COUNTERTREND_RECLASSIFICATION"
    payload["success_claim"] = False
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    current = payload["candidate22_overall"]
    semantic = payload["semantic_rejected_far_state"]
    lines = [
        "# Candidate 22 contested-countertrend resolution development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Candidate 21 | {previous['daily_geometric_growth']:.6%} | {previous['nav_multiple']:.6f} | {previous['trades']} | {previous['wins']}/{previous['losses']} | {previous['win_rate']:.2%} | {previous['active_weeks']} | {fmt(previous['payoff_ratio'])} |",
        f"| Candidate 22 | {current['daily_geometric_growth']:.6%} | {current['nav_multiple']:.6f} | {current['trades']} | {current['wins']}/{current['losses']} | {current['win_rate']:.2%} | {current['active_weeks']} | {fmt(current.get('payoff_ratio'))} |",
        "",
        "## Route decision",
        "",
        f"- Old moderate-countertrend fills remaining: {old_fills}",
        f"- Contested watches armed: {contested_armed}",
        f"- Incremental semantic-resolution fills: {semantic['trades']}",
        f"- Incremental semantic-resolution PnL: {semantic['net_pnl']:.2f} USDT",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
