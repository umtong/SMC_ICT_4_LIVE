#!/usr/bin/env python3
"""Aggregate Candidate 16 failed-FAR continuation development evidence."""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from decimal import Decimal
import json
import math
from pathlib import Path
from typing import Any

WEEKS = tuple(f"H{i:02d}" for i in range(1, 17))
DAYS = 112
NEW_KIND = "FAILED_FAR_ACCEPTANCE_CONTINUATION"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def pnl_value(raw: Any) -> float:
    return float(str(raw).split()[0])


def map_filled_scenarios(folder: Path) -> tuple[list[dict[str, Any]], Counter[str]]:
    plans = load(folder / "submitted_plans.json").get("plans", [])
    plan_by_id = {str(plan["scenario_id"]): plan for plan in plans}
    lifecycle = load(folder / "order_lifecycle.json").get("events", [])
    opening_order_to_scenario: dict[str, str] = {}
    pending_scenario: str | None = None
    exit_classification: Counter[str] = Counter()
    for event in lifecycle:
        kind = str(event.get("type", ""))
        if kind == "GLOBAL_ENTRY_SUBMITTED":
            pending_scenario = str(event["scenario_id"])
        elif kind == "ORDER_FILLED" and pending_scenario is not None:
            opening_order_to_scenario.setdefault(str(event["client_order_id"]), pending_scenario)
        elif kind == "GLOBAL_ENTRY_FILLED":
            pending_scenario = None
        elif kind == "NATIVE_EXIT_CLASSIFIED":
            exit_classification[str(event.get("reason", "UNKNOWN"))] += 1

    output: list[dict[str, Any]] = []
    with (folder / "positions.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            scenario_id = opening_order_to_scenario.get(str(row.get("opening_order_id", "")))
            plan = plan_by_id.get(str(scenario_id), {})
            details = plan.get("details", {}) or {}
            scenario_kind = str(details.get("scenario_kind", plan.get("scenario", "UNKNOWN")))
            pnl = pnl_value(row.get("realized_pnl", 0))
            output.append(
                {
                    "scenario_id": scenario_id,
                    "scenario_kind": scenario_kind,
                    "symbol": str(plan.get("symbol", str(row.get("instrument_id", "")).split("-PERP", 1)[0])),
                    "direction": str(plan.get("direction", row.get("entry", "UNKNOWN"))),
                    "pnl": pnl,
                    "win": pnl > 0.0,
                    "loss": pnl < 0.0,
                    "entry_model": details.get("entry_model"),
                    "stop_model": details.get("stop_model"),
                    "parent_scenario_id": details.get("parent_scenario_id"),
                }
            )
    return output, exit_classification


def outcome_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    wins = [r["pnl"] for r in records if r["pnl"] > 0.0]
    losses = [r["pnl"] for r in records if r["pnl"] < 0.0]
    total = len(records)
    average_win = sum(wins) / len(wins) if wins else None
    average_loss = abs(sum(losses) / len(losses)) if losses else None
    payoff = (
        average_win / average_loss
        if average_win is not None and average_loss not in (None, 0.0)
        else None
    )
    return {
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / total if total else 0.0,
        "net_pnl": sum(r["pnl"] for r in records),
        "average_win": average_win,
        "average_loss": average_loss,
        "payoff_ratio": payoff,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate15", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    baseline_raw = load(args.baseline)
    candidate15 = load(args.candidate15)["variants"]["SWEEP_MARKET"]
    nav_multiple = Decimal("1")
    trades = wins = losses = active_weeks = 0
    safety = True
    week_rows: list[dict[str, Any]] = []
    all_positions: list[dict[str, Any]] = []
    state_events: Counter[str] = Counter()
    terminal_reasons: Counter[str] = Counter()
    exit_classification: Counter[str] = Counter()
    submitted_kinds: Counter[str] = Counter()
    symbols: Counter[str] = Counter()
    positive_logs: dict[str, float] = {}

    safety_keys = (
        "evidence_complete",
        "metric_recalculation_passed",
        "risk_budget_passed",
        "global_slot_passed",
        "partial_entry_protection_passed",
        "no_liquidation_passed",
        "engine_errors_absent",
    )

    for week in WEEKS:
        folder = args.root / week
        metrics = load(folder / "metrics.json")
        audit = load(folder / "audit.json")
        ratio = Decimal(str(metrics["final_nav"])) / Decimal(str(metrics["starting_nav"]))
        nav_multiple *= ratio
        log_growth = math.log(float(ratio))
        if log_growth > 0.0:
            positive_logs[week] = log_growth
        count = int(metrics.get("closed_trades", 0))
        trades += count
        wins += int(metrics.get("wins", 0))
        losses += int(metrics.get("losses", 0))
        active_weeks += int(count > 0)
        week_safety = all(audit.get(key) is True for key in safety_keys)
        safety = safety and week_safety
        positions, exits = map_filled_scenarios(folder)
        all_positions.extend({"week": week, **record} for record in positions)
        exit_classification.update(exits)
        for plan in load(folder / "submitted_plans.json").get("plans", []):
            details = plan.get("details", {}) or {}
            kind = str(details.get("scenario_kind", plan.get("scenario", "UNKNOWN")))
            submitted_kinds[kind] += 1
            symbols[str(plan.get("symbol", "UNKNOWN"))] += 1
        with (folder / "scenario_events.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                scenario_id = str(event.get("scenario_id", ""))
                event_type = str(event.get("event_type", ""))
                reason = str(event.get("reason_code", ""))
                if "-FAILED-FAR-" in scenario_id:
                    state_events[event_type] += 1
                    if event_type == "FAILED_FAR_CONTINUATION_TERMINAL":
                        terminal_reasons[reason] += 1
        week_rows.append(
            {
                "week": week,
                "daily_geometric_growth": metrics.get("daily_geometric_growth"),
                "nav_ratio": float(ratio),
                "closed_trades": count,
                "wins": metrics.get("wins", 0),
                "losses": metrics.get("losses", 0),
                "new_state_filled_trades": sum(1 for r in positions if r["scenario_kind"] == NEW_KIND),
                "safety": week_safety,
            }
        )

    daily_growth = float(nav_multiple ** (Decimal("1") / Decimal(DAYS)) - Decimal("1"))
    positive_total = sum(positive_logs.values())
    concentration = max(positive_logs.values(), default=0.0) / positive_total if positive_total > 0 else 0.0
    by_kind: dict[str, dict[str, Any]] = {}
    for kind in sorted({record["scenario_kind"] for record in all_positions}):
        by_kind[kind] = outcome_summary([r for r in all_positions if r["scenario_kind"] == kind])
    new_state = by_kind.get(NEW_KIND, outcome_summary([]))
    overall = outcome_summary(all_positions)
    overall.update(
        {
            "observed_calendar_days": DAYS,
            "daily_geometric_growth": daily_growth,
            "nav_multiple": float(nav_multiple),
            "active_weeks": active_weeks,
            "all_safety_audits": safety,
            "positive_growth_concentration": concentration,
        }
    )
    baseline = {
        "daily_geometric_growth": baseline_raw["daily_geometric_growth"],
        "nav_multiple": baseline_raw["pooled_nav_multiple"],
        "trades": baseline_raw["closed_trades"],
        "wins": baseline_raw["wins"],
        "losses": baseline_raw["losses"],
        "win_rate": baseline_raw["win_rate"],
    }
    structural_recovery = (
        safety
        and daily_growth > 0.0
        and overall["net_pnl"] > 0.0
        and new_state["trades"] >= 4
        and new_state["net_pnl"] > 0.0
        and new_state["win_rate"] >= 0.50
        and active_weeks >= baseline_raw["active_weeks"]
    )
    payload = {
        "schema": "candidate-16-failed-far-continuation-development-v1",
        "classification": "DEVELOPMENT_REPLAY_ONLY_NO_SUCCESS_CLAIM",
        "research_question": "When a completed FAR is truly invalidated through its sweep-extreme stop, does same-side acceptance, failed-boundary retest and reacceleration create an independent continuation edge?",
        "baseline_candidate14": baseline,
        "candidate15_sweep_market": {
            key: candidate15[key]
            for key in (
                "daily_geometric_growth", "nav_multiple", "closed_trades", "wins",
                "losses", "win_rate", "active_weeks", "payoff_ratio"
            )
        },
        "candidate16_overall": overall,
        "outcomes_by_state": by_kind,
        "failed_far_state": new_state,
        "submitted_plan_kinds": dict(sorted(submitted_kinds.items())),
        "submitted_symbols": dict(sorted(symbols.items())),
        "state_event_counts": dict(sorted(state_events.items())),
        "state_terminal_reasons": dict(sorted(terminal_reasons.items())),
        "native_exit_classification": dict(sorted(exit_classification.items())),
        "weeks": week_rows,
        "structural_recovery": structural_recovery,
        "decision": "FREEZE_BEFORE_NEW_UNTOUCHED_HOLDOUTS" if structural_recovery else "REJECT_OR_REDESIGN_FAILED_FAR_STATE",
        "success_claim": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payoff = overall.get("payoff_ratio")
    new_payoff = new_state.get("payoff_ratio")
    lines = [
        "# Candidate 16 failed-FAR continuation development",
        "",
        "**DEVELOPMENT REPLAY ONLY — NO SUCCESS CLAIM**",
        "",
        "| Candidate | Daily geo | NAV | Trades | W/L | Win rate | Active weeks | Payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Candidate 14 | {float(baseline['daily_geometric_growth']):.6%} | {float(baseline['nav_multiple']):.6f} | {baseline['trades']} | {baseline['wins']}/{baseline['losses']} | {float(baseline['win_rate']):.2%} | {baseline_raw['active_weeks']} | {float(baseline_raw['payoff_ratio']):.3f} |",
        f"| Candidate 15 SWEEP_MARKET | {candidate15['daily_geometric_growth']:.6%} | {candidate15['nav_multiple']:.6f} | {candidate15['closed_trades']} | {candidate15['wins']}/{candidate15['losses']} | {candidate15['win_rate']:.2%} | {candidate15['active_weeks']} | {candidate15['payoff_ratio']:.3f} |",
        f"| Candidate 16 | {daily_growth:.6%} | {float(nav_multiple):.6f} | {overall['trades']} | {overall['wins']}/{overall['losses']} | {overall['win_rate']:.2%} | {active_weeks} | {'n/a' if payoff is None else f'{payoff:.3f}'} |",
        "",
        "## Incremental failed-FAR state",
        "",
        f"- Trades: {new_state['trades']}",
        f"- Wins/losses: {new_state['wins']}/{new_state['losses']}",
        f"- Win rate: {new_state['win_rate']:.2%}",
        f"- Net PnL: {new_state['net_pnl']:.2f} USDT",
        f"- Payoff: {'n/a' if new_payoff is None else f'{new_payoff:.3f}'}",
        f"- Decision: `{payload['decision']}`",
    ]
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
