#!/usr/bin/env python3
"""Attribute Candidate 28 quarter-hour context/internal-reload evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

import aggregate_candidate16_failed_far as attribution
import aggregate_candidate25_clean_allocator as base

KIND = "QUARTER_HOUR_CONTEXT_INTERNAL_RELOAD"
WEEKS = tuple(f"H{i:02d}" for i in range(1, 17))


def fmt(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
    candidate27 = load(args.candidate27)

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
                or "-QHR-" in str(record.get("scenario_id"))
            ):
                records.append({"week": week, **record})
                per_week[week]["fills"] += 1
        with (folder / "scenario_events.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                scenario_id = str(event.get("scenario_id", ""))
                if "-QHR-" not in scenario_id:
                    continue
                event_type = str(event.get("event_type", ""))
                reason = str(event.get("reason_code", ""))
                events[event_type] += 1
                if event_type == "QUARTER_HOUR_RELOAD_CONTEXT_ARMED":
                    per_week[week]["contexts"] += 1
                elif event_type == "QUARTER_HOUR_DELIVERY_LEG_CONFIRMED":
                    per_week[week]["extensions"] += 1
                elif event_type == "QUARTER_HOUR_INTERNAL_RELOAD_RAID":
                    per_week[week]["raids"] += 1
                elif event_type == "QUARTER_HOUR_INTERNAL_RELOAD_RECLAIMED":
                    per_week[week]["reclaims"] += 1
                elif event_type == "TRADE_PLAN_CONFIRMED":
                    per_week[week]["plans"] += 1
                elif event_type == "QUARTER_HOUR_RELOAD_TERMINAL":
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
        "QUARTER_HOUR_RELOAD_CONTEXT_ARMED",
        "QUARTER_HOUR_DELIVERY_LEG_CONFIRMED",
        "QUARTER_HOUR_INTERNAL_RELOAD_RAID",
        "QUARTER_HOUR_INTERNAL_RELOAD_RECLAIMED",
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

    payload["schema"] = "candidate-28-quarter-hour-context-internal-reload-development-v1"
    payload["classification"] = "DEVELOPMENT_REPLAY_ONLY_NO_SUCCESS_CLAIM"
    payload["research_question"] = (
        "After a UTC quarter-hour opening imbalance and a separate completed "
        "opening-extreme extension establish medium-horizon delivery context, "
        "does a later opposite-flow raid of a newly formed pre-known five-minute "
        "internal pivot, followed by reclaim and still-later reacceleration, "
        "create a tradeable reload toward frozen external liquidity?"
    )
    payload["candidate28_overall"] = payload.pop("candidate25_overall")
    payload["candidate25_reference"] = reference(candidate25, "candidate25_overall")
    payload["candidate26_rejected_reference"] = {
        "decision": candidate26.get("decision"),
        **reference(candidate26, "candidate26_overall"),
        "incremental_state": {
            key: candidate26["internal_liquidity_raid_state"].get(key)
            for key in ("trades", "wins", "losses", "win_rate", "net_pnl", "payoff_ratio")
        },
    }
    payload["candidate27_rejected_reference"] = {
        "decision": candidate27.get("decision"),
        **reference(candidate27, "candidate27_overall"),
        "incremental_state": {
            key: candidate27["quarter_hour_delivery_state"].get(key)
            for key in ("trades", "wins", "losses", "win_rate", "net_pnl", "payoff_ratio")
        },
    }
    payload["quarter_hour_reload_state"] = state
    payload["quarter_hour_reload_outcomes_by_direction"] = by_direction
    payload["quarter_hour_reload_outcomes_by_symbol"] = by_symbol
    payload["quarter_hour_reload_event_counts"] = dict(sorted(events.items()))
    payload["quarter_hour_reload_stage_counts"] = stage_counts
    payload["quarter_hour_reload_stage_conversions"] = conversions
    payload["quarter_hour_reload_terminal_reasons"] = dict(sorted(terminal_reasons.items()))
    payload["quarter_hour_reload_market_rejection_reasons"] = dict(
        sorted(rejection_reasons.items())
    )
    payload["scenario_contract"] = {
        "context": (
            "first fully completed minute after UTC quarter-hour plus a later "
            "completed opening-extreme extension"
        ),
        "entry_liquidity": (
            "previously known five-minute pivot formed after the opening context "
            "inside the confirmed delivery leg"
        ),
        "raid": "opposite aggressor flow through the internal pivot",
        "transition": "pivot reclaim followed by a still-later reacceleration bar",
        "portfolio_confirmation": (
            "unchanged synchronized-market AAC gate measured from the reload raid"
        ),
        "target": "frozen strict pre-existing live external liquidity pool",
        "invalidation": "actual reload raid extreme plus existing ATR buffer",
        "entry": "native market parent after reacceleration",
        "cost_gate": "unchanged after-cost minimum 1.25R",
        "risk": "exact 3% current-NAV planned loss",
        "portfolio_slot": 1,
        "reused_internal_pivot": False,
        "new_numeric_threshold": None,
    }

    current = payload["candidate28_overall"]
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
    payload["quarter_hour_reload_route_supported"] = route_supported
    payload["structural_recovery"] = route_supported
    payload["decision"] = (
        "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
        if route_supported
        else "REJECT_OR_REDESIGN_QUARTER_HOUR_INTERNAL_RELOAD"
    )
    payload["success_claim"] = False

    for row in payload.get("weeks", []):
        week = str(row.get("week"))
        counts = per_week.get(week, Counter())
        row["quarter_hour_reload_contexts"] = int(counts["contexts"])
        row["quarter_hour_reload_extensions"] = int(counts["extensions"])
        row["quarter_hour_reload_raids"] = int(counts["raids"])
        row["quarter_hour_reload_reclaims"] = int(counts["reclaims"])
        row["quarter_hour_reload_plans"] = int(counts["plans"])
        row["quarter_hour_reload_filled_trades"] = int(counts["fills"])

    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Candidate 28 quarter-hour context/internal-reload development",
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
            f"| Candidate 28 | {current['daily_geometric_growth']:.6%} | "
            f"{current['nav_multiple']:.6f} | {current['trades']} | "
            f"{current['wins']}/{current['losses']} | "
            f"{current['win_rate']:.2%} | {current['active_weeks']} | "
            f"{fmt(current.get('payoff_ratio'))} |"
        ),
        "",
        "## Incremental quarter-hour reload state",
        "",
        f"- Contexts armed: {stage_counts['QUARTER_HOUR_RELOAD_CONTEXT_ARMED']}",
        f"- Delivery legs confirmed: {stage_counts['QUARTER_HOUR_DELIVERY_LEG_CONFIRMED']}",
        f"- Internal raids: {stage_counts['QUARTER_HOUR_INTERNAL_RELOAD_RAID']}",
        f"- Reclaims: {stage_counts['QUARTER_HOUR_INTERNAL_RELOAD_RECLAIMED']}",
        f"- Plans confirmed: {stage_counts['TRADE_PLAN_CONFIRMED']}",
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
