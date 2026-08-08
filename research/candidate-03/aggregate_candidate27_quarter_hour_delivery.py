#!/usr/bin/env python3
"""Attribute Candidate 27 quarter-hour delivery development evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

import aggregate_candidate16_failed_far as attribution
import aggregate_candidate25_clean_allocator as base

KIND = "QUARTER_HOUR_SYNCHRONIZED_DELIVERY"
WEEKS = tuple(f"H{i:02d}" for i in range(1, 17))


def fmt(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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

    all_records: list[dict[str, Any]] = []
    events: Counter[str] = Counter()
    terminal_reasons: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    openings_by_week: Counter[str] = Counter()
    confirmations_by_week: Counter[str] = Counter()
    fills_by_week: Counter[str] = Counter()
    for week in WEEKS:
        folder = args.root / week
        records, _ = attribution.map_filled_scenarios(folder)
        for record in records:
            if (
                str(record.get("scenario_kind")) == KIND
                or "-QHD-" in str(record.get("scenario_id"))
            ):
                all_records.append({"week": week, **record})
                fills_by_week[week] += 1
        with (folder / "scenario_events.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                scenario_id = str(event.get("scenario_id", ""))
                if "-QHD-" not in scenario_id:
                    continue
                event_type = str(event.get("event_type", ""))
                reason = str(event.get("reason_code", ""))
                events[event_type] += 1
                if event_type == "QUARTER_HOUR_OPENING_IMBALANCE":
                    openings_by_week[week] += 1
                elif event_type == "TRADE_PLAN_CONFIRMED":
                    confirmations_by_week[week] += 1
                elif event_type == "QUARTER_HOUR_DELIVERY_TERMINAL":
                    terminal_reasons[reason] += 1
                elif event_type == "ENTRY_PLAN_REJECTED":
                    rejection_reasons[reason] += 1

    state = attribution.outcome_summary(all_records)
    state["active_weeks"] = len({str(record["week"]) for record in all_records})
    state["fills_by_week"] = dict(sorted(fills_by_week.items()))

    by_direction = {
        direction: attribution.outcome_summary(
            [record for record in all_records if record.get("direction") == direction]
        )
        for direction in sorted({str(record.get("direction")) for record in all_records})
    }
    by_symbol = {
        symbol: attribution.outcome_summary(
            [record for record in all_records if record.get("symbol") == symbol]
        )
        for symbol in sorted({str(record.get("symbol")) for record in all_records})
    }

    payload["schema"] = "candidate-27-quarter-hour-synchronized-delivery-development-v1"
    payload["classification"] = "DEVELOPMENT_REPLAY_ONLY_NO_SUCCESS_CLAIM"
    payload["research_question"] = (
        "Do UTC quarter-hour opening bars with directional taker-flow imbalance, "
        "relative activity and price acceptance, followed by a separate completed "
        "opening-extreme extension and the unchanged synchronized-market AAC gate, "
        "deliver price toward a pre-existing external liquidity objective?"
    )
    payload["candidate27_overall"] = payload.pop("candidate25_overall")
    payload["candidate25_reference"] = {
        key: candidate25["candidate25_overall"][key]
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
    payload["candidate26_rejected_reference"] = {
        "decision": candidate26.get("decision"),
        "daily_geometric_growth": candidate26["candidate26_overall"][
            "daily_geometric_growth"
        ],
        "nav_multiple": candidate26["candidate26_overall"]["nav_multiple"],
        "trades": candidate26["candidate26_overall"]["trades"],
        "win_rate": candidate26["candidate26_overall"]["win_rate"],
        "internal_state": {
            key: candidate26["internal_liquidity_raid_state"].get(key)
            for key in (
                "trades",
                "wins",
                "losses",
                "win_rate",
                "net_pnl",
                "payoff_ratio",
            )
        },
    }
    payload["quarter_hour_delivery_state"] = state
    payload["quarter_hour_outcomes_by_direction"] = by_direction
    payload["quarter_hour_outcomes_by_symbol"] = by_symbol
    payload["quarter_hour_event_counts"] = dict(sorted(events.items()))
    payload["quarter_hour_terminal_reasons"] = dict(sorted(terminal_reasons.items()))
    payload["quarter_hour_market_rejection_reasons"] = dict(
        sorted(rejection_reasons.items())
    )
    payload["quarter_hour_openings_by_week"] = dict(sorted(openings_by_week.items()))
    payload["quarter_hour_confirmations_by_week"] = dict(
        sorted(confirmations_by_week.items())
    )
    payload["scenario_contract"] = {
        "clock_context": (
            "first fully completed one-minute bar after each UTC 00/15/30/45 boundary"
        ),
        "opening_state": [
            "directional candle body using the existing displacement-body control",
            "directional close location using the existing acceptance control",
            "directional taker-flow imbalance using the existing acceptance-flow control",
            "relative activity using the existing minimum-volume control",
        ],
        "transition_confirmation": (
            "a later completed bar extends beyond the opening extreme with the "
            "existing reacceleration body, flow and close-location controls"
        ),
        "portfolio_confirmation": "unchanged synchronized-market AAC semantic gate",
        "target": "nearest strict pre-existing live external liquidity pool",
        "invalidation": "opening-leg or interim pullback extreme plus existing ATR buffer",
        "entry": "native market parent after the later completed extension",
        "cost_gate": "unchanged after-cost minimum 1.25R",
        "risk": "exact 3% current-NAV planned loss",
        "portfolio_slot": 1,
        "new_numeric_threshold": None,
    }

    current = payload["candidate27_overall"]
    reference = payload["candidate25_reference"]
    route_supported = (
        bool(current.get("all_safety_audits"))
        and state["trades"] >= 8
        and state["net_pnl"] > 0.0
        and state["active_weeks"] >= 4
        and float(current.get("daily_geometric_growth", 0.0))
        > float(reference["daily_geometric_growth"])
        and float(current.get("nav_multiple", 0.0))
        > float(reference["nav_multiple"])
        and float(current.get("positive_growth_concentration", 1.0)) <= 0.60
    )
    payload["quarter_hour_route_supported"] = route_supported
    payload["structural_recovery"] = route_supported
    payload["decision"] = (
        "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
        if route_supported
        else "REJECT_OR_REDESIGN_QUARTER_HOUR_DELIVERY"
    )
    payload["success_claim"] = False

    for row in payload.get("weeks", []):
        week = str(row.get("week"))
        row["quarter_hour_openings"] = int(openings_by_week.get(week, 0))
        row["quarter_hour_confirmations"] = int(confirmations_by_week.get(week, 0))
        row["quarter_hour_filled_trades"] = int(fills_by_week.get(week, 0))

    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Candidate 27 quarter-hour synchronized delivery development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| Candidate 25 | {reference['daily_geometric_growth']:.6%} | "
            f"{reference['nav_multiple']:.6f} | {reference['trades']} | "
            f"{reference['wins']}/{reference['losses']} | "
            f"{reference['win_rate']:.2%} | {reference['active_weeks']} | "
            f"{fmt(reference['payoff_ratio'])} |"
        ),
        (
            f"| Candidate 27 | {current['daily_geometric_growth']:.6%} | "
            f"{current['nav_multiple']:.6f} | {current['trades']} | "
            f"{current['wins']}/{current['losses']} | "
            f"{current['win_rate']:.2%} | {current['active_weeks']} | "
            f"{fmt(current.get('payoff_ratio'))} |"
        ),
        "",
        "## Incremental quarter-hour state",
        "",
        f"- Openings armed: {events.get('QUARTER_HOUR_OPENING_IMBALANCE', 0)}",
        f"- Plans confirmed: {events.get('TRADE_PLAN_CONFIRMED', 0)}",
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
