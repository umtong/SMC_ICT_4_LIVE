#!/usr/bin/env python3
"""Attribute Candidate 26 internal-liquidity raid development evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import aggregate_candidate16_failed_far as attribution
import aggregate_candidate25_clean_allocator as base

KIND = "EXTERNAL_DRAW_INTERNAL_LIQUIDITY_RAID"
WEEKS = tuple(f"H{i:02d}" for i in range(1, 17))


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
    parser.add_argument("--candidate25", type=Path, required=True)
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

    payload = json.loads(args.output.read_text(encoding="utf-8"))
    prior = json.loads(args.candidate25.read_text(encoding="utf-8"))

    all_records: list[dict[str, object]] = []
    events: Counter[str] = Counter()
    terminal_reasons: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    fills_by_week: Counter[str] = Counter()
    detections_by_week: Counter[str] = Counter()
    confirmations_by_week: Counter[str] = Counter()
    for week in WEEKS:
        folder = args.root / week
        records, _ = attribution.map_filled_scenarios(folder)
        for record in records:
            if str(record.get("scenario_kind")) == KIND or "-ILR-" in str(record.get("scenario_id")):
                all_records.append({"week": week, **record})
                fills_by_week[week] += 1
        with (folder / "scenario_events.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                scenario_id = str(event.get("scenario_id", ""))
                if "-ILR-" not in scenario_id:
                    continue
                event_type = str(event.get("event_type", ""))
                reason = str(event.get("reason_code", ""))
                events[event_type] += 1
                if event_type == "INTERNAL_LIQUIDITY_RAID_DETECTED":
                    detections_by_week[week] += 1
                if event_type == "TRADE_PLAN_CONFIRMED":
                    confirmations_by_week[week] += 1
                if event_type == "INTERNAL_RAID_TERMINAL":
                    terminal_reasons[reason] += 1
                if event_type == "ENTRY_PLAN_REJECTED":
                    rejection_reasons[reason] += 1

    internal = attribution.outcome_summary(all_records)
    internal_active_weeks = len({str(record["week"]) for record in all_records})
    internal["active_weeks"] = internal_active_weeks
    internal["fills_by_week"] = dict(sorted(fills_by_week.items()))

    payload["schema"] = "candidate-26-external-draw-internal-liquidity-raid-development-v1"
    payload["research_question"] = (
        "When the causal external liquidity map resolves a dominant draw, does a "
        "raid and reclaim of a previously known opposite-side five-minute internal "
        "pivot, followed by later displacement and independent synchronized-market "
        "approval, create a repeatable continuation edge toward that external draw?"
    )
    payload["candidate26_overall"] = payload.pop("candidate25_overall")
    payload["candidate25_reference"] = {
        key: prior["candidate25_overall"][key]
        for key in (
            "daily_geometric_growth", "nav_multiple", "trades", "wins", "losses",
            "win_rate", "active_weeks", "payoff_ratio", "positive_growth_concentration",
        )
    }
    payload["internal_liquidity_raid_state"] = internal
    payload["internal_raid_event_counts"] = dict(sorted(events.items()))
    payload["internal_raid_terminal_reasons"] = dict(sorted(terminal_reasons.items()))
    payload["internal_raid_market_rejection_reasons"] = dict(sorted(rejection_reasons.items()))
    payload["internal_raid_detections_by_week"] = dict(sorted(detections_by_week.items()))
    payload["internal_raid_confirmations_by_week"] = dict(sorted(confirmations_by_week.items()))
    payload["scenario_contract"] = {
        "context_liquidity": (
            "pre-existing live external pool selected by the frozen causal draw resolver"
        ),
        "entry_liquidity": (
            "previously known opposite-side five-minute internal pivot"
        ),
        "required_sequence": [
            "active regional decision window",
            "dominant live external draw",
            "opposite internal pivot raid with aggressor flow",
            "completed reclaim of the raided pivot",
            "later completed displacement through the raid episode",
            "unchanged AAC synchronized cross-market approval",
        ],
        "entry": "native market parent after completed displacement",
        "invalidation": "actual internal raid extreme plus existing ATR buffer",
        "target": "frozen pre-existing external draw",
        "cost_gate": "unchanged after-cost minimum 1.25R",
        "risk": "exact 3% current-NAV planned loss",
        "portfolio_slot": 1,
        "new_numeric_threshold": None,
    }

    current = payload["candidate26_overall"]
    reference = payload["candidate25_reference"]
    route_supported = (
        bool(current.get("all_safety_audits"))
        and internal["trades"] >= 4
        and internal["wins"] >= internal["losses"]
        and internal["net_pnl"] > 0.0
        and internal_active_weeks >= 3
        and float(current.get("daily_geometric_growth", 0.0))
        > float(reference["daily_geometric_growth"])
        and float(current.get("positive_growth_concentration", 1.0)) <= 0.60
    )
    payload["internal_raid_route_supported"] = route_supported
    payload["structural_recovery"] = route_supported
    payload["decision"] = (
        "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
        if route_supported
        else "REJECT_OR_REDESIGN_INTERNAL_LIQUIDITY_RAID"
    )
    payload["success_claim"] = False

    for row in payload.get("weeks", []):
        week = str(row.get("week"))
        row["internal_raid_detections"] = int(detections_by_week.get(week, 0))
        row["internal_raid_confirmations"] = int(confirmations_by_week.get(week, 0))
        row["internal_raid_filled_trades"] = int(fills_by_week.get(week, 0))

    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Candidate 26 external-draw internal-liquidity raid development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Candidate 25 | {reference['daily_geometric_growth']:.6%} | {reference['nav_multiple']:.6f} | {reference['trades']} | {reference['wins']}/{reference['losses']} | {reference['win_rate']:.2%} | {reference['active_weeks']} | {fmt(reference['payoff_ratio'])} |",
        f"| Candidate 26 | {current['daily_geometric_growth']:.6%} | {current['nav_multiple']:.6f} | {current['trades']} | {current['wins']}/{current['losses']} | {current['win_rate']:.2%} | {current['active_weeks']} | {fmt(current.get('payoff_ratio'))} |",
        "",
        "## Incremental internal-liquidity raid state",
        "",
        f"- Detections: {events.get('INTERNAL_LIQUIDITY_RAID_DETECTED', 0)}",
        f"- Reclaims: {events.get('INTERNAL_RAID_RECLAIM_CONFIRMED', 0)}",
        f"- Plans confirmed: {events.get('TRADE_PLAN_CONFIRMED', 0)}",
        f"- Filled trades: {internal['trades']}",
        f"- Wins/losses: {internal['wins']}/{internal['losses']}",
        f"- Win rate: {internal['win_rate']:.2%}",
        f"- Net PnL: {internal['net_pnl']:.2f} USDT",
        f"- Payoff: {fmt(internal.get('payoff_ratio'))}",
        f"- Active weeks: {internal_active_weeks}",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
