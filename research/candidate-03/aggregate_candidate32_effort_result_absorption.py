#!/usr/bin/env python3
"""Attribute Candidate 32 repeated-pool effort/result absorption evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

import aggregate_candidate16_failed_far as attribution
import aggregate_candidate25_clean_allocator as base

KIND = "REPEATED_INTERNAL_POOL_EFFORT_RESULT_ABSORPTION"
WEEKS = tuple(f"H{i:02d}" for i in range(1, 17))


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
    parser.add_argument("--candidate31", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    saved = sys.argv
    try:
        sys.argv = [
            "aggregate_candidate25_clean_allocator.py",
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

    payload = load(args.output)
    candidate25 = load(args.candidate25)
    candidate26 = load(args.candidate26)
    candidate31 = load(args.candidate31)

    records: list[dict[str, Any]] = []
    events: Counter[str] = Counter()
    terminal_reasons: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    per_week: dict[str, Counter[str]] = {week: Counter() for week in WEEKS}

    for week in WEEKS:
        folder = args.root / week
        positions, _ = attribution.map_filled_scenarios(folder)
        for record in positions:
            if (
                str(record.get("scenario_kind")) == KIND
                or "-ERA-" in str(record.get("scenario_id"))
            ):
                records.append({"week": week, **record})
                per_week[week]["fills"] += 1
        with (folder / "scenario_events.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                scenario_id = str(event.get("scenario_id", ""))
                if "-ERA-" not in scenario_id:
                    continue
                event_type = str(event.get("event_type", ""))
                reason = str(event.get("reason_code", ""))
                events[event_type] += 1
                if event_type == "EFFORT_RESULT_FIRST_TEST":
                    per_week[week]["first_tests"] += 1
                elif event_type == "EFFORT_RESULT_FIRST_RECLAIM":
                    per_week[week]["first_reclaims"] += 1
                elif event_type == "EFFORT_RESULT_ABSORPTION_CONFIRMED":
                    per_week[week]["absorptions"] += 1
                elif event_type == "EFFORT_RESULT_FINAL_RECLAIM":
                    per_week[week]["final_reclaims"] += 1
                elif event_type == "TRADE_PLAN_CONFIRMED":
                    per_week[week]["plans"] += 1
                elif event_type == "EFFORT_RESULT_ABSORPTION_TERMINAL":
                    terminal_reasons[reason] += 1
                elif event_type == "ENTRY_PLAN_REJECTED":
                    rejection_reasons[reason] += 1

    state = attribution.outcome_summary(records)
    state["active_weeks"] = len({str(record["week"]) for record in records})
    state["fills_by_week"] = {
        week: int(per_week[week]["fills"])
        for week in WEEKS
        if per_week[week]["fills"]
    }
    by_direction = {
        direction: attribution.outcome_summary(
            [record for record in records if record.get("direction") == direction]
        )
        for direction in sorted({str(record.get("direction")) for record in records})
    }
    by_symbol = {
        symbol: attribution.outcome_summary(
            [record for record in records if record.get("symbol") == symbol]
        )
        for symbol in sorted({str(record.get("symbol")) for record in records})
    }
    stage_names = (
        "EFFORT_RESULT_FIRST_TEST",
        "EFFORT_RESULT_FIRST_RECLAIM",
        "EFFORT_RESULT_ABSORPTION_CONFIRMED",
        "EFFORT_RESULT_FINAL_RECLAIM",
        "TRADE_PLAN_CONFIRMED",
        "ENTRY_FILLED",
        "POSITION_TERMINAL",
    )
    stage_counts = {name: int(events.get(name, 0)) for name in stage_names}
    conversions: dict[str, float | None] = {}
    for previous, current in zip(stage_names[:-1], stage_names[1:], strict=True):
        denominator = stage_counts[previous]
        conversions[f"{previous}_TO_{current}"] = (
            stage_counts[current] / denominator if denominator else None
        )

    payload["schema"] = "candidate-32-effort-result-absorption-development-v1"
    payload["classification"] = "DEVELOPMENT_REPLAY_ONLY_NO_SUCCESS_CLAIM"
    payload["research_question"] = (
        "After a pre-known five-minute internal pool is swept and reclaimed, does "
        "a second test with at least as much aggressor flow but no greater "
        "ATR-normalized penetration identify absorption that can reverse toward "
        "frozen external liquidity?"
    )
    payload["candidate32_overall"] = payload.pop("candidate25_overall")
    payload["candidate25_reference"] = reference(candidate25, "candidate25_overall")
    payload["candidate26_single_raid_rejected_reference"] = {
        "decision": candidate26.get("decision"),
        **reference(candidate26, "candidate26_overall"),
        "incremental_state": candidate26.get("internal_liquidity_raid_state"),
    }
    payload["candidate31_quarter_hour_family_reference"] = {
        "decision": candidate31.get("decision"),
        "route_supported": candidate31.get("quarter_hour_reclaim_stop_route_supported"),
        **reference(candidate31, "candidate31_overall"),
        "incremental_state": candidate31.get("quarter_hour_reclaim_stop_state"),
    }
    payload["effort_result_absorption_state"] = state
    payload["effort_result_absorption_outcomes_by_direction"] = by_direction
    payload["effort_result_absorption_outcomes_by_symbol"] = by_symbol
    payload["effort_result_absorption_event_counts"] = dict(sorted(events.items()))
    payload["effort_result_absorption_stage_counts"] = stage_counts
    payload["effort_result_absorption_stage_conversions"] = conversions
    payload["effort_result_absorption_terminal_reasons"] = dict(sorted(terminal_reasons.items()))
    payload["effort_result_absorption_market_rejection_reasons"] = dict(
        sorted(rejection_reasons.items())
    )
    payload["scenario_contract"] = {
        "first_test": "pre-known five-minute internal pivot trade-through with existing activity and flow thresholds",
        "first_transition": "later completed reclaim of the same pivot",
        "second_test": "same pivot; absolute aggressor flow not lower; ATR penetration not greater",
        "final_confirmation": "still-later reclaim with opposite flow, body and close location",
        "target": "frozen nearest live pre-existing external pool opposite the swept side",
        "invalidation": "maximum repeated raid extreme plus existing ATR buffer",
        "entry": "native market parent after final reclaim",
        "portfolio_confirmation": "unchanged FAR synchronized-market semantic gate",
        "cost_gate": "unchanged after-cost minimum 1.25R",
        "risk": "exact 3% current-NAV planned loss",
        "portfolio_slot": 1,
        "new_numeric_threshold": None,
    }

    current = payload["candidate32_overall"]
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
    payload["effort_result_absorption_route_supported"] = route_supported
    payload["structural_recovery"] = route_supported
    payload["decision"] = (
        "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
        if route_supported
        else "REJECT_OR_REDESIGN_EFFORT_RESULT_ABSORPTION"
    )
    payload["success_claim"] = False

    for row in payload.get("weeks", []):
        week = str(row.get("week"))
        counts = per_week.get(week, Counter())
        row["effort_result_first_tests"] = int(counts["first_tests"])
        row["effort_result_first_reclaims"] = int(counts["first_reclaims"])
        row["effort_result_absorptions"] = int(counts["absorptions"])
        row["effort_result_final_reclaims"] = int(counts["final_reclaims"])
        row["effort_result_plans"] = int(counts["plans"])
        row["effort_result_filled_trades"] = int(counts["fills"])

    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Candidate 32 repeated-pool effort/result absorption",
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
            f"| Candidate 32 | {current['daily_geometric_growth']:.6%} | "
            f"{current['nav_multiple']:.6f} | {current['trades']} | "
            f"{current['wins']}/{current['losses']} | "
            f"{current['win_rate']:.2%} | {current['active_weeks']} | "
            f"{fmt(current.get('payoff_ratio'))} |"
        ),
        "",
        "## Incremental effort/result state",
        "",
        f"- First tests: {stage_counts['EFFORT_RESULT_FIRST_TEST']}",
        f"- First reclaims: {stage_counts['EFFORT_RESULT_FIRST_RECLAIM']}",
        f"- Absorptions: {stage_counts['EFFORT_RESULT_ABSORPTION_CONFIRMED']}",
        f"- Final reclaims: {stage_counts['EFFORT_RESULT_FINAL_RECLAIM']}",
        f"- Plans: {stage_counts['TRADE_PLAN_CONFIRMED']}",
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
