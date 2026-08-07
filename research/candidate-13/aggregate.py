#!/usr/bin/env python3
"""Aggregate predeclared Candidate 13 holdouts without changing strategy logic."""
from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from math import exp, log
from pathlib import Path
from typing import Any


SAFETY_KEYS = (
    "evidence_complete",
    "metric_recalculation_passed",
    "risk_budget_passed",
    "global_slot_passed",
    "partial_entry_protection_passed",
    "no_liquidation_passed",
    "engine_errors_absent",
)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def dec(value: Any) -> Decimal:
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in {"none", "null", "nan", "nat"}:
        raise InvalidOperation(text)
    return Decimal(text.split()[0])


def realized_pnls(path: Path) -> list[Decimal]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    column = None
    if rows:
        column = next(
            (name for name in ("realized_pnl", "pnl") if name in rows[0]),
            None,
        )
    if column is None:
        return []
    values: list[Decimal] = []
    for row in rows:
        try:
            values.append(dec(row[column]))
        except (InvalidOperation, KeyError):
            continue
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    protocol = load_object(args.protocol)
    gate = protocol["aggregate_gate"]
    holdouts = protocol["selection"]["holdouts"]
    ordered = sorted(holdouts, key=lambda name: date.fromisoformat(holdouts[name]["start"]))

    records: list[dict[str, Any]] = []
    missing: list[str] = []
    all_pnls: list[Decimal] = []
    for week in ordered:
        root = args.results / week
        metrics_path = root / "metrics.json"
        audit_path = root / "audit.json"
        if not metrics_path.is_file() or not audit_path.is_file():
            missing.append(week)
            continue
        metrics = load_object(metrics_path)
        audit = load_object(audit_path)
        start_nav = dec(metrics["starting_nav"])
        final_nav = dec(metrics["final_nav"])
        ratio = final_nav / start_nav
        if ratio <= 0:
            log_growth = float("-inf")
        else:
            log_growth = log(float(ratio))
        pnls = realized_pnls(root / "positions.csv")
        all_pnls.extend(pnls)
        safety = all(audit.get(key) is True for key in SAFETY_KEYS)
        records.append(
            {
                "week": week,
                "start": holdouts[week]["start"],
                "end_exclusive": holdouts[week]["end_exclusive"],
                "role": holdouts[week]["role"],
                "starting_nav": str(start_nav),
                "final_nav": str(final_nav),
                "nav_ratio": float(ratio),
                "log_growth": log_growth,
                "daily_geometric_growth": float(metrics.get("daily_geometric_growth", 0.0)),
                "closed_trades": int(metrics.get("closed_trades", 0)),
                "wins": int(metrics.get("wins", 0)),
                "losses": int(metrics.get("losses", 0)),
                "win_rate": float(metrics.get("win_rate", 0.0)),
                "closed_trade_max_drawdown": float(metrics.get("closed_trade_max_drawdown", 0.0)),
                "safety_audit_passed": safety,
                "audit_classification": audit.get("classification"),
                "scenario_counts": metrics.get("scenario_counts", {}),
                "symbol_counts": metrics.get("symbol_counts", {}),
            }
        )

    completed = len(records)
    total_days = completed * int(protocol["selection"].get("evaluation_days", 7))
    if not total_days:
        total_days = completed * 7
    finite_logs = [record["log_growth"] for record in records if record["log_growth"] != float("-inf")]
    pooled_log = sum(finite_logs) if len(finite_logs) == completed else float("-inf")
    pooled_daily = exp(pooled_log / total_days) - 1.0 if total_days and pooled_log != float("-inf") else -1.0
    total_trades = sum(record["closed_trades"] for record in records)
    wins = sum(record["wins"] for record in records)
    losses = sum(record["losses"] for record in records)
    win_rate = wins / total_trades if total_trades else 0.0
    active_weeks = sum(record["closed_trades"] > 0 for record in records)
    max_drawdown = max((record["closed_trade_max_drawdown"] for record in records), default=0.0)
    positive_logs = [max(0.0, record["log_growth"]) for record in records]
    positive_total = sum(positive_logs)
    concentration = max(positive_logs, default=0.0) / positive_total if positive_total > 0 else 1.0

    positive_pnls = [value for value in all_pnls if value > 0]
    negative_pnls = [value for value in all_pnls if value < 0]
    if positive_pnls and negative_pnls:
        payoff_ratio: float | None = float(
            (sum(positive_pnls) / len(positive_pnls))
            / abs(sum(negative_pnls) / len(negative_pnls))
        )
    elif positive_pnls:
        payoff_ratio = None
    else:
        payoff_ratio = 0.0
    payoff_for_gate = float("inf") if positive_pnls and not negative_pnls else float(payoff_ratio or 0.0)

    checks = {
        "all_holdouts_complete": completed == len(holdouts) and not missing,
        "all_safety_audits": all(record["safety_audit_passed"] for record in records) and completed == len(holdouts),
        "daily_geometric_growth": pooled_daily >= float(gate["minimum_daily_geometric_growth"]),
        "closed_trades": total_trades >= int(gate["minimum_closed_trades"]),
        "active_weeks": active_weeks >= int(gate["minimum_active_weeks"]),
        "win_rate": win_rate >= float(gate["minimum_win_rate"]),
        "payoff_ratio": payoff_for_gate >= float(gate["minimum_payoff_ratio"]),
        "max_drawdown": max_drawdown <= float(gate["maximum_trade_path_drawdown"]),
        "growth_concentration": concentration <= float(gate["maximum_positive_log_growth_share_from_one_week"]),
    }
    success = all(checks.values())
    failed = [name for name, passed in checks.items() if not passed]
    classification = (
        "CANDIDATE13_AGGREGATE_GATE_PASSED"
        if success
        else "CANDIDATE13_AGGREGATE_GATE_FAILED"
    )

    result = {
        "schema": "candidate-13-aggregate-evidence-v1",
        "candidate": protocol["candidate"],
        "classification": classification,
        "success_claim": success,
        "strategy_parameters_changed_after_holdout_freeze": False,
        "holdout_count": len(holdouts),
        "completed_holdouts": completed,
        "missing_holdouts": missing,
        "observed_calendar_days": total_days,
        "pooled_nav_multiple": exp(pooled_log) if pooled_log != float("-inf") else 0.0,
        "daily_geometric_growth": pooled_daily,
        "closed_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "payoff_ratio": payoff_ratio,
        "all_closed_trades_won": total_trades > 0 and losses == 0,
        "active_weeks": active_weeks,
        "maximum_weekly_closed_trade_drawdown": max_drawdown,
        "maximum_positive_log_growth_share_from_one_week": concentration,
        "all_safety_audits_passed": checks["all_safety_audits"],
        "checks": checks,
        "failed_checks": failed,
        "weeks": records,
        "gate": gate,
    }
    write_json(args.output, result)

    lines = [
        "# Candidate 13 aggregate holdout result",
        "",
        f"**{classification}**",
        "",
        f"- success_claim: `{success}`",
        f"- daily_geometric_growth: `{pooled_daily:.10f}`",
        f"- pooled_nav_multiple: `{result['pooled_nav_multiple']:.10f}`",
        f"- closed_trades: `{total_trades}`",
        f"- wins / losses: `{wins} / {losses}`",
        f"- win_rate: `{win_rate:.6f}`",
        f"- payoff_ratio: `{payoff_ratio}`",
        f"- active_weeks: `{active_weeks} / {len(holdouts)}`",
        f"- maximum_weekly_closed_trade_drawdown: `{max_drawdown:.10f}`",
        f"- maximum_positive_log_growth_share_from_one_week: `{concentration:.10f}`",
        "",
        "## Gate checks",
    ]
    lines.extend(f"- {name}: `{passed}`" for name, passed in checks.items())
    lines.extend(("", "## Weekly evidence"))
    for record in records:
        lines.append(
            f"- {record['week']} ({record['start']}): "
            f"daily_geo={record['daily_geometric_growth']:.6f}, "
            f"trades={record['closed_trades']}, "
            f"W/L={record['wins']}/{record['losses']}, "
            f"safety={record['safety_audit_passed']}"
        )
    if missing:
        lines.extend(("", "## Missing holdouts"))
        lines.extend(f"- {week}" for week in missing)
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
