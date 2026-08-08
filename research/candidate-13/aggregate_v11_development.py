#!/usr/bin/env python3
"""Aggregate Candidate 13 V11 exposed development without a success claim."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from math import exp, log
from pathlib import Path
from typing import Any, Iterable

SAFETY_KEYS = (
    "evidence_complete",
    "metric_recalculation_passed",
    "risk_budget_passed",
    "global_slot_passed",
    "partial_entry_protection_passed",
    "no_liquidation_passed",
    "engine_errors_absent",
)
EVENT_KEYS = (
    "QHI_COMMON_FLOW_EVENT_OBSERVED",
    "QHF_ATTEMPTED_INITIATIVE_ACTIVATED",
    "QHF_ATTEMPTED_INITIATIVE_EXTENDED",
    "QHF_ATTEMPTED_INITIATIVE_TERMINATED",
    "QHF_MARKETWIDE_FAILURE_CONFIRMED",
    "QHF_REVERSAL_PLAN_CONFIRMED",
    "QHF_ENTRY_FILLED",
    "QHF_TRADE_TERMINAL",
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


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
    if not rows:
        return []
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


def event_counts(path: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if not path.is_file():
        return {name: 0 for name in EVENT_KEYS}
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            payload = json.loads(line)
            counts[str(payload.get("event_type", "UNKNOWN"))] += 1
    return {name: int(counts.get(name, 0)) for name in EVENT_KEYS}


def lifecycle_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    payload = load_object(path)
    counts = Counter(
        str(event.get("type", "UNKNOWN"))
        for event in payload.get("events", [])
        if isinstance(event, dict)
    )
    return dict(sorted(counts.items()))


def merge_counts(records: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
    materialized = list(records)
    names = sorted({name for record in materialized for name in record.get(key, {})})
    return {
        name: sum(int(record.get(key, {}).get(name, 0)) for record in materialized)
        for name in names
    }


def aggregate(results: Path, protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    holdouts = protocol["selection"]["holdouts"]
    ordered = sorted(
        holdouts,
        key=lambda name: date.fromisoformat(holdouts[name]["start"]),
    )
    records: list[dict[str, Any]] = []
    all_pnls: list[Decimal] = []
    missing: list[str] = []
    for interval in ordered:
        root = results / interval
        metrics_path = root / "metrics.json"
        audit_path = root / "audit.json"
        if not metrics_path.is_file() or not audit_path.is_file():
            missing.append(interval)
            continue
        metrics = load_object(metrics_path)
        audit_payload = load_object(audit_path)
        start_nav = dec(metrics["starting_nav"])
        final_nav = dec(metrics["final_nav"])
        ratio = final_nav / start_nav
        values = realized_pnls(root / "positions.csv")
        all_pnls.extend(values)
        records.append(
            {
                "interval": interval,
                "start": holdouts[interval]["start"],
                "end_exclusive": holdouts[interval]["end_exclusive"],
                "role": holdouts[interval]["role"],
                "starting_nav": str(start_nav),
                "final_nav": str(final_nav),
                "nav_ratio": float(ratio),
                "log_growth": log(float(ratio)) if ratio > 0 else float("-inf"),
                "daily_geometric_growth": float(
                    metrics.get("daily_geometric_growth", 0.0),
                ),
                "closed_trades": int(metrics.get("closed_trades", 0)),
                "wins": int(metrics.get("wins", 0)),
                "losses": int(metrics.get("losses", 0)),
                "win_rate": float(metrics.get("win_rate", 0.0)),
                "closed_trade_max_drawdown": float(
                    metrics.get("closed_trade_max_drawdown", 0.0),
                ),
                "submitted_plans": int(metrics.get("submitted_plans", 0)),
                "module_counts": metrics.get("module_counts", {}),
                "scenario_counts": metrics.get("scenario_counts", {}),
                "symbol_counts": metrics.get("symbol_counts", {}),
                "skip_reasons": metrics.get("skip_reasons", {}),
                "event_counts": event_counts(root / "scenario_events.jsonl"),
                "lifecycle_counts": lifecycle_counts(root / "order_lifecycle.json"),
                "engine_error_count": len(metrics.get("engine_errors", [])),
                "safety_audit_passed": all(
                    audit_payload.get(key) is True for key in SAFETY_KEYS
                ),
                "audit_classification": audit_payload.get("classification"),
            },
        )

    total_days = len(records) * int(protocol["selection"].get("evaluation_days", 7))
    finite = [
        record["log_growth"]
        for record in records
        if record["log_growth"] != float("-inf")
    ]
    pooled_log = sum(finite) if len(finite) == len(records) else float("-inf")
    daily = (
        exp(pooled_log / total_days) - 1.0
        if total_days and pooled_log != float("-inf")
        else -1.0
    )
    trades = sum(record["closed_trades"] for record in records)
    wins = sum(record["wins"] for record in records)
    losses = sum(record["losses"] for record in records)
    win_rate = wins / trades if trades else 0.0
    active = sum(record["closed_trades"] > 0 for record in records)
    max_dd = max(
        (record["closed_trade_max_drawdown"] for record in records),
        default=0.0,
    )
    positive = [value for value in all_pnls if value > 0]
    negative = [value for value in all_pnls if value < 0]
    if positive and negative:
        payoff: float | None = float(
            (sum(positive) / len(positive))
            / abs(sum(negative) / len(negative)),
        )
    elif positive:
        payoff = None
    else:
        payoff = 0.0
    payoff_gate = (
        float("inf") if positive and not negative else float(payoff or 0.0)
    )
    positive_logs = [max(0.0, record["log_growth"]) for record in records]
    positive_total = sum(positive_logs)
    concentration = (
        max(positive_logs, default=0.0) / positive_total
        if positive_total
        else 1.0
    )

    gate = protocol["development_gate"]
    checks = {
        "all_intervals_complete": len(records) == len(holdouts) and not missing,
        "all_safety_audits": (
            len(records) == len(holdouts)
            and all(record["safety_audit_passed"] for record in records)
        ),
        "closed_trades": trades >= int(gate["minimum_closed_trades"]),
        "active_intervals": active >= int(gate["minimum_active_intervals"]),
        "daily_geometric_growth": daily >= float(
            gate["minimum_daily_geometric_growth"],
        ),
        "win_rate": win_rate >= float(gate["minimum_win_rate"]),
        "payoff_ratio": payoff_gate >= float(gate["minimum_payoff_ratio"]),
        "max_drawdown": max_dd <= float(gate["maximum_trade_path_drawdown"]),
        "growth_concentration": concentration <= float(
            gate["maximum_positive_log_growth_share_from_one_interval"],
        ),
    }
    advance = all(checks.values())
    result = {
        "schema": "candidate-13-v11-failed-initiative-development-aggregate-v1",
        "candidate": protocol["candidate"],
        "development_only": True,
        "success_claim": False,
        "classification": (
            "ADVANCE_TO_BROADER_EXPOSED_SCREEN"
            if advance
            else "REJECT_OR_REDESIGN"
        ),
        "completed_intervals": len(records),
        "missing_intervals": missing,
        "observed_calendar_days": total_days,
        "pooled_nav_multiple": (
            exp(pooled_log) if pooled_log != float("-inf") else 0.0
        ),
        "daily_geometric_growth": daily,
        "closed_trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "payoff_ratio": payoff,
        "active_intervals": active,
        "maximum_trade_path_drawdown": max_dd,
        "maximum_positive_log_growth_share_from_one_interval": concentration,
        "module_counts": merge_counts(records, "module_counts"),
        "scenario_counts": merge_counts(records, "scenario_counts"),
        "symbol_counts": merge_counts(records, "symbol_counts"),
        "event_counts": merge_counts(records, "event_counts"),
        "lifecycle_counts": merge_counts(records, "lifecycle_counts"),
        "engine_error_count": sum(record["engine_error_count"] for record in records),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "intervals": records,
        "development_gate": gate,
    }
    write_json(output, result)

    lines = [
        "# Candidate 13 V11 quarter-hour failed initiative development",
        "",
        f"**{result['classification']}**",
        "",
        "This is exposed development evidence and cannot support a success claim.",
        "",
        f"- daily geometric growth: `{daily:.10f}`",
        f"- pooled NAV multiple: `{result['pooled_nav_multiple']:.10f}`",
        f"- closed trades: `{trades}`",
        f"- wins / losses: `{wins} / {losses}`",
        f"- win rate: `{win_rate:.6f}`",
        f"- payoff ratio: `{payoff}`",
        f"- active intervals: `{active} / {len(holdouts)}`",
        f"- module counts: `{result['module_counts']}`",
        f"- event counts: `{result['event_counts']}`",
        f"- engine errors: `{result['engine_error_count']}`",
        "",
        "## Checks",
    ]
    lines.extend(f"- {name}: `{passed}`" for name, passed in checks.items())
    lines.extend(("", "## Intervals"))
    for record in records:
        lines.append(
            f"- {record['interval']} {record['start']}: "
            f"trades={record['closed_trades']}, "
            f"W/L={record['wins']}/{record['losses']}, "
            f"daily_geo={record['daily_geometric_growth']:.6f}, "
            f"modules={record['module_counts']}, "
            f"events={record['event_counts']}"
        )
    output.with_name("RESULT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    aggregate(args.results.resolve(), args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
