#!/usr/bin/env python3
"""Independent evidence audit for Candidate 11 microstructure runs."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any

REQUIRED = (
    "run.json", "data_manifest.json", "metrics.json", "orders.csv",
    "positions.csv", "account.csv", "scenario_events.jsonl",
    "submitted_plans.json", "order_lifecycle.json",
)


def dec(value: Any) -> Decimal:
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in {"nan", "nat", "none", "null"}:
        raise InvalidOperation(f"invalid number {value!r}")
    return Decimal(text.split()[0])


def rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def timestamp_ns(value: Any) -> Decimal | None:
    if value in (None, "", "None", "nan", "NaT"):
        return None
    try:
        number = dec(value)
        if abs(number) >= Decimal("1000000000000"):
            return number
    except InvalidOperation:
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return Decimal(str(parsed.timestamp())) * Decimal("1000000000")
    except (ValueError, TypeError, OverflowError):
        return None


def audit(root: Path, week: str) -> dict[str, Any]:
    files = {
        name: {
            "exists": (root / name).is_file(),
            "bytes": (root / name).stat().st_size if (root / name).is_file() else 0,
        }
        for name in REQUIRED
    }
    reasons = [
        f"missing or empty evidence: {name}"
        for name, value in files.items()
        if not value["exists"] or value["bytes"] == 0
    ]
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8")) if (root / "metrics.json").is_file() else {}
    metric_ok = True
    recalculated: dict[str, Any] = {}
    try:
        start = dec(metrics["starting_nav"])
        final = dec(metrics["final_nav"])
        days = dec(metrics["evaluation_calendar_days"])
        growth = float((final / start) ** (Decimal(1) / days) - Decimal(1))
        total = float(final / start - Decimal(1))
        recalculated = {
            "starting_nav": str(start),
            "final_nav": str(final),
            "evaluation_days": str(days),
            "daily_geometric_growth": growth,
            "net_return": total,
        }
        if abs(float(metrics.get("daily_geometric_growth")) - growth) > 1e-10:
            metric_ok = False
            reasons.append("daily geometric growth mismatch")
        if abs(float(metrics.get("net_return")) - total) > 1e-10:
            metric_ok = False
            reasons.append("net return mismatch")
    except (KeyError, InvalidOperation, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        metric_ok = False
        reasons.append(f"NAV metrics cannot be recomputed: {exc}")

    payload = json.loads((root / "submitted_plans.json").read_text(encoding="utf-8")) if (root / "submitted_plans.json").is_file() else {}
    plans = payload.get("plans", payload if isinstance(payload, list) else [])
    risk_ok = True
    for index, plan in enumerate(plans):
        try:
            nav = dec(plan["nav_before"])
            budget = dec(plan["planned_loss_budget"])
            expected = dec(plan["expected_total_loss"])
            exact = nav * Decimal("0.03")
            tolerance = max(Decimal("0.01"), abs(exact) * Decimal("0.000001"))
            if abs(budget - exact) > tolerance or expected > budget + tolerance:
                risk_ok = False
                reasons.append(f"plan[{index}] violates exact 3% NAV loss budget")
        except (KeyError, InvalidOperation) as exc:
            risk_ok = False
            reasons.append(f"plan[{index}] lacks risk evidence: {exc}")

    intervals: list[tuple[Decimal, Decimal]] = []
    for row in rows(root / "positions.csv"):
        opened = timestamp_ns(row.get("ts_opened") or row.get("ts_init"))
        closed = timestamp_ns(row.get("ts_closed") or row.get("ts_last"))
        if opened is not None and closed is not None and closed >= opened:
            intervals.append((opened, closed))
    intervals.sort()
    slot_ok = all(next_open >= current_close for (_, current_close), (next_open, _) in zip(intervals, intervals[1:]))
    if not slot_ok:
        reasons.append("overlapping positions violate one-slot invariant")

    liquidation_ok = metrics.get("liquidation_detected") not in (True, "true", "True", 1)
    if not liquidation_ok:
        reasons.append("liquidation detected")
    errors_ok = not metrics.get("engine_errors")
    if not errors_ok:
        reasons.append(f"engine errors present: {metrics.get('engine_errors')}")
    chronology_ok = True
    last = -1
    if (root / "scenario_events.jsonl").is_file():
        with (root / "scenario_events.jsonl").open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                event = json.loads(line)
                ts = int(event.get("observed_ts_ns", -1))
                if ts < last:
                    chronology_ok = False
                    reasons.append(f"event chronology regressed at line {line_number}")
                    break
                last = ts
    else:
        chronology_ok = False

    evidence_ok = not any(not value["exists"] or value["bytes"] == 0 for value in files.values())
    implementation_ok = all((evidence_ok, metric_ok, risk_ok, slot_ok, liquidation_ok, errors_ok, chronology_ok))
    closed = int(metrics.get("closed_trades") or 0)
    win_rate = float(metrics.get("win_rate") or 0.0)
    payoff = metrics.get("payoff_ratio")
    growth = float(metrics.get("daily_geometric_growth") or 0.0)
    drawdown = float(metrics.get("closed_trade_max_drawdown") or 0.0)
    screening = (
        implementation_ok
        and closed >= 10
        and win_rate >= 0.80
        and (payoff is None or float(payoff) >= 1.20)
        and growth >= 0.01
        and drawdown <= 0.20
    )
    if not implementation_ok:
        classification = "IMPLEMENTATION_OR_EVIDENCE_FAILURE"
    elif closed == 0:
        classification = "LOGIC_OR_EXECUTION_FAILURE_NO_CLOSED_TRADES"
    elif screening:
        classification = "MICROSTRUCTURE_SCREENING_GATE_PASSED"
    else:
        classification = "MICROSTRUCTURE_ALPHA_OR_FREQUENCY_FAILURE"
    return {
        "schema": "candidate-11-microstructure-audit-v1",
        "week": week,
        "classification": classification,
        "implementation_passed": implementation_ok,
        "screening_gate_passed": screening,
        "evidence_complete": evidence_ok,
        "metric_recalculation_passed": metric_ok,
        "risk_budget_passed": risk_ok,
        "global_slot_passed": slot_ok,
        "no_liquidation_passed": liquidation_ok,
        "engine_errors_absent": errors_ok,
        "event_chronology_passed": chronology_ok,
        "reasons": reasons,
        "recalculated": recalculated,
        "metrics": metrics,
        "evidence_files": files,
        "success_claim": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--week", choices=("M1", "M2", "M3"), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.result_dir, args.week)
    output = args.output or args.result_dir / "audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 2 if result["classification"] == "IMPLEMENTATION_OR_EVIDENCE_FAILURE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
