#!/usr/bin/env python3
"""Attribute Candidate 19 pre-entry semantic-rejection continuation evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import aggregate_candidate16_failed_far as base

NEW_KIND = "FAILED_FAR_STRICT_EXTERNAL_ACCEPTANCE_CONTINUATION"


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

    all_positions = []
    semantic_events: Counter[str] = Counter()
    semantic_terminal_reasons: Counter[str] = Counter()
    semantic_seed_reasons: Counter[str] = Counter()
    semantic_week_fills: Counter[str] = Counter()
    for week in base.WEEKS:
        folder = args.root / week
        positions, _ = base.map_filled_scenarios(folder)
        for record in positions:
            row = {"week": week, **record}
            all_positions.append(row)
            if "-REJECTED-FAR-" in str(record.get("scenario_id", "")):
                semantic_week_fills[week] += 1
        with (folder / "scenario_events.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                scenario_id = str(event.get("scenario_id", ""))
                if "-REJECTED-FAR-" not in scenario_id:
                    continue
                event_type = str(event.get("event_type", ""))
                reason = str(event.get("reason_code", ""))
                semantic_events[event_type] += 1
                if event_type == "SEMANTIC_REJECTED_FAR_CONTINUATION_ARMED":
                    semantic_seed_reasons[reason] += 1
                if event_type in {
                    "SEMANTIC_REJECTED_FAR_CONTINUATION_TERMINAL",
                    "FAILED_FAR_CONTINUATION_TERMINAL",
                    "ENTRY_PLAN_REJECTED",
                }:
                    semantic_terminal_reasons[reason] += 1

    semantic_records = [
        row for row in all_positions
        if "-REJECTED-FAR-" in str(row.get("scenario_id", ""))
    ]
    post_stop_records = [
        row for row in all_positions
        if "-FAILED-FAR-" in str(row.get("scenario_id", ""))
    ]
    semantic_summary = base.outcome_summary(semantic_records)
    post_stop_summary = base.outcome_summary(post_stop_records)
    combined_summary = payload.pop("failed_far_state")

    payload["schema"] = "candidate-19-semantic-rejected-far-continuation-development-v1"
    payload["research_question"] = (
        "When a complete local FAR is rejected before entry because cross-market "
        "semantics do not support the reversal, does later local acceptance beyond "
        "the swept boundary, defended retest and synchronized reacceleration form "
        "an independent continuation auction?"
    )
    payload["candidate19_overall"] = payload.pop("candidate16_overall")
    payload["candidate17_reference"] = {
        key: prior["candidate17_overall"][key]
        for key in (
            "daily_geometric_growth", "nav_multiple", "trades", "wins", "losses",
            "win_rate", "active_weeks", "payoff_ratio", "positive_growth_concentration",
        )
    }
    payload["combined_far_invalidation_state"] = combined_summary
    payload["post_stop_failed_far_state"] = post_stop_summary
    payload["semantic_rejected_far_state"] = semantic_summary
    payload["post_stop_event_counts"] = payload.pop("state_event_counts")
    payload["post_stop_terminal_reasons"] = payload.pop("state_terminal_reasons")
    payload["semantic_rejected_far_event_counts"] = dict(sorted(semantic_events.items()))
    payload["semantic_rejected_far_terminal_reasons"] = dict(
        sorted(semantic_terminal_reasons.items())
    )
    payload["semantic_rejected_far_seed_reasons"] = dict(sorted(semantic_seed_reasons.items()))
    payload["semantic_rejected_far_fills_by_week"] = dict(sorted(semantic_week_fills.items()))
    payload["hypothesis_contract"] = {
        "eligible_seed_rejections": [
            "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM",
            "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION",
        ],
        "immediate_inversion": False,
        "required_sequence": [
            "complete local FAR plan rejected before submission",
            "two completed closes beyond the swept boundary",
            "completed defended retest of that boundary",
            "later local reacceleration with flow/body/location",
            "unchanged AAC synchronized cross-market approval",
        ],
        "target": "nearest strict pre-existing unconsumed external pool",
        "risk": "exact 3% current-NAV planned loss after costs",
        "portfolio_slot": 1,
    }

    overall = payload["candidate19_overall"]
    structural_recovery = (
        bool(overall.get("all_safety_audits"))
        and float(overall.get("daily_geometric_growth", 0.0)) > 0.0
        and semantic_summary["trades"] >= 4
        and semantic_summary["net_pnl"] > 0.0
        and semantic_summary["win_rate"] >= 0.50
        and int(overall.get("active_weeks", 0)) >= int(payload["candidate17_reference"]["active_weeks"])
        and float(overall.get("positive_growth_concentration", 1.0)) <= 0.60
    )
    payload["structural_recovery"] = structural_recovery
    payload["decision"] = (
        "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS"
        if structural_recovery
        else "REJECT_OR_REDESIGN_SEMANTIC_REJECTED_FAR_STATE"
    )
    payload["success_claim"] = False
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    current = payload["candidate19_overall"]
    previous = payload["candidate17_reference"]
    lines = [
        "# Candidate 19 semantic-rejected FAR continuation development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Candidate 17 | {previous['daily_geometric_growth']:.6%} | {previous['nav_multiple']:.6f} | {previous['trades']} | {previous['wins']}/{previous['losses']} | {previous['win_rate']:.2%} | {previous['active_weeks']} | {fmt(previous['payoff_ratio'])} |",
        f"| Candidate 19 | {current['daily_geometric_growth']:.6%} | {current['nav_multiple']:.6f} | {current['trades']} | {current['wins']}/{current['losses']} | {current['win_rate']:.2%} | {current['active_weeks']} | {fmt(current.get('payoff_ratio'))} |",
        "",
        "## Incremental semantic-rejected FAR state",
        "",
        f"- Armed watches: {semantic_events.get('SEMANTIC_REJECTED_FAR_CONTINUATION_ARMED', 0)}",
        f"- Filled trades: {semantic_summary['trades']}",
        f"- Wins/losses: {semantic_summary['wins']}/{semantic_summary['losses']}",
        f"- Win rate: {semantic_summary['win_rate']:.2%}",
        f"- Net PnL: {semantic_summary['net_pnl']:.2f} USDT",
        f"- Payoff: {fmt(semantic_summary.get('payoff_ratio'))}",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
