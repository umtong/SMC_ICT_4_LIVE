#!/usr/bin/env python3
"""Audit Candidate 11 evidence without simulating orders, fills, or NAV."""
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
    "positions.csv", "account.csv", "scenario_events.jsonl", "submitted_plans.json",
)


def dec(value: Any) -> Decimal:
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in {"none", "null", "nan", "nat"}:
        raise InvalidOperation(f"invalid number {value!r}")
    return Decimal(text.split()[0])


def pick(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return None


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def plans(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("plans", "submitted_plans", "records"):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


def csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def time_value(row: dict[str, str], names: tuple[str, ...]) -> Decimal | None:
    for name in names:
        value = row.get(name)
        if value in (None, "", "None", "nan", "NaT"):
            continue
        try:
            return dec(value)
        except InvalidOperation:
            pass
    return None


def timestamp_ns(row: dict[str, str], names: tuple[str, ...]) -> Decimal | None:
    """Parse either integer nanoseconds or an ISO-8601 report timestamp."""
    for name in names:
        value = row.get(name)
        if value in (None, "", "None", "nan", "NaT"):
            continue
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
            continue
    return None


def audit(root: Path, week: str) -> dict[str, Any]:
    files = {name: {"exists": (root / name).is_file(), "bytes": (root / name).stat().st_size if (root / name).is_file() else 0} for name in REQUIRED}
    missing = [name for name, record in files.items() if not record["exists"] or record["bytes"] == 0]
    reasons = [f"missing or empty evidence: {name}" for name in missing]
    metrics: dict[str, Any] = {}
    if (root / "metrics.json").is_file():
        value = load_json(root / "metrics.json")
        if isinstance(value, dict):
            metrics = value
        else:
            reasons.append("metrics.json is not an object")

    recalculated: dict[str, Any] = {}
    metric_ok = True
    try:
        start = dec(pick(metrics, "starting_nav", "initial_nav"))
        final = dec(pick(metrics, "final_nav", "ending_nav"))
        days = dec(pick(metrics, "evaluation_days", "evaluation_calendar_days"))
        daily = float(final / start) ** (1.0 / float(days)) - 1.0
        total = float(final / start - Decimal(1))
        recalculated = {"starting_nav": str(start), "final_nav": str(final), "evaluation_days": float(days), "daily_geometric_growth": daily, "net_return": total}
        recorded = pick(metrics, "daily_geometric_growth", "daily_geo")
        if recorded is None or abs(float(recorded) - daily) > 1e-10:
            metric_ok = False
            reasons.append(f"daily growth mismatch: recorded={recorded!r}, recalculated={daily!r}")
    except (InvalidOperation, ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        metric_ok = False
        reasons.append(f"NAV metrics cannot be independently recomputed: {exc}")

    plan_payload = load_json(root / "submitted_plans.json") if (root / "submitted_plans.json").is_file() else {}
    plan_rows = plans(plan_payload)
    risk_ok = True
    for index, plan in enumerate(plan_rows):
        try:
            nav = dec(plan.get("nav_before"))
            budget = dec(plan.get("planned_loss_budget"))
            expected = dec(plan.get("expected_total_loss"))
            exact = nav * Decimal("0.03")
            tolerance = max(Decimal("0.01"), abs(exact) * Decimal("0.000001"))
            if abs(budget - exact) > tolerance or expected > budget + tolerance:
                risk_ok = False
                reasons.append(f"plan[{index}] violates the exact 3% NAV loss budget")
        except InvalidOperation as exc:
            risk_ok = False
            reasons.append(f"plan[{index}] lacks risk evidence: {exc}")

    intervals: list[tuple[Decimal, Decimal]] = []
    for row in csv_rows(root / "positions.csv"):
        opened = time_value(row, ("ts_opened", "ts_open", "open_time", "entry_time", "ts_init"))
        closed = time_value(row, ("ts_closed", "ts_close", "close_time", "exit_time", "ts_last"))
        if opened is not None and closed is not None and closed >= opened:
            intervals.append((opened, closed))
    intervals.sort()
    slot_ok = all(next_open >= closed for (_, closed), (next_open, _) in zip(intervals, intervals[1:]))
    if not slot_ok:
        reasons.append("overlapping position intervals violate the global one-position invariant")

    order_rows = csv_rows(root / "orders.csv")
    position_rows = csv_rows(root / "positions.csv")
    partial_expiry_records: list[dict[str, Any]] = []
    protection_ok = True
    max_close_delay_ns = Decimal("60000000000")
    for row in order_rows:
        try:
            quantity = dec(row.get("quantity"))
            filled = dec(row.get("filled_qty"))
        except InvalidOperation:
            continue
        tags = str(row.get("tags", "")).upper()
        is_partial_expiry = (
            str(row.get("status", "")).upper() == "EXPIRED"
            and str(row.get("time_in_force", "")).upper() == "GTD"
            and "ENTRY" in tags
            and Decimal("0") < filled < quantity
        )
        if not is_partial_expiry:
            continue
        expired_ns = timestamp_ns(row, ("ts_last", "expire_time_ns"))
        matching_positions: list[tuple[Decimal, dict[str, str]]] = []
        if expired_ns is not None:
            for position_row in position_rows:
                if position_row.get("instrument_id") != row.get("instrument_id"):
                    continue
                opened_ns = timestamp_ns(position_row, ("ts_opened", "ts_init"))
                position_closed_ns = timestamp_ns(position_row, ("ts_closed", "ts_last"))
                if (
                    opened_ns is not None
                    and position_closed_ns is not None
                    and opened_ns <= expired_ns <= position_closed_ns
                ):
                    matching_positions.append((opened_ns, position_row))
        position = max(matching_positions, key=lambda item: item[0])[1] if matching_positions else None
        closed_ns = None if position is None else timestamp_ns(position, ("ts_closed", "ts_last"))
        delay_ns = None if expired_ns is None or closed_ns is None else closed_ns - expired_ns
        passed = delay_ns is not None and Decimal("0") <= delay_ns <= max_close_delay_ns
        partial_expiry_records.append({
            "instrument_id": row.get("instrument_id"),
            "position_id": row.get("position_id"),
            "quantity": str(quantity),
            "filled_qty": str(filled),
            "expired_ns": None if expired_ns is None else str(expired_ns),
            "closed_ns": None if closed_ns is None else str(closed_ns),
            "close_delay_ns": None if delay_ns is None else str(delay_ns),
            "fail_closed_within_one_bar": passed,
        })
        if not passed:
            protection_ok = False
            reasons.append(
                f"partially filled GTD entry {row.get('position_id')} remained open beyond one bar after expiry",
            )

    liquidation = pick(metrics, "liquidation_detected", "was_liquidated")
    no_liquidation = liquidation not in (True, "true", "True", 1)
    if not no_liquidation:
        reasons.append("liquidation detected")
    engine_errors = metrics.get("engine_errors")
    engine_ok = not (isinstance(engine_errors, list) and engine_errors)
    if not engine_ok:
        reasons.append(f"engine or order errors present: {engine_errors}")

    evidence_ok = not missing
    submitted = int(pick(metrics, "submitted_plans", "submitted_plan_count") or len(plan_rows))
    closed_trades = int(pick(metrics, "closed_trades", "trades") or 0)
    promising = pick(metrics, "promising_gate_passed") is True
    complete = pick(metrics, "complete_gate_passed", "complete_candidate_gate_passed") is True
    recorded_success = metrics.get("success_claim") is True

    if not all((evidence_ok, metric_ok, risk_ok, slot_ok, protection_ok, no_liquidation, engine_ok)):
        classification = "IMPLEMENTATION_OR_EVIDENCE_FAILURE"
    elif submitted and not closed_trades:
        classification = "EXECUTION_FAILURE_NO_CLOSED_TRADES"
        reasons.append("plans were submitted but Nautilus reported no closed trades")
    elif not submitted:
        classification = "LOGIC_FAILURE_NO_EXECUTABLE_PLANS"
        reasons.append("the causal state machine produced no executable plans")
    elif not promising:
        classification = "LOGIC_OR_FREQUENCY_FAILURE"
        reasons.append("the frozen W1 promising gate did not pass")
    elif complete:
        classification = "W1_COMPLETE_GATE_PASSED"
    else:
        classification = "W1_PROMISING_ADVANCE_TO_INDEPENDENT_WEEK"

    advance = week == "W1" and classification in {"W1_COMPLETE_GATE_PASSED", "W1_PROMISING_ADVANCE_TO_INDEPENDENT_WEEK"}
    success_allowed = week != "W1" and complete and recorded_success and all((evidence_ok, metric_ok, risk_ok, slot_ok, protection_ok, no_liquidation, engine_ok))
    if week == "W1" and (complete or recorded_success):
        reasons.append("W1 is a screening week and cannot alone establish durable project success")

    return {
        "schema": "candidate-11-evidence-audit-v1",
        "week": week,
        "classification": classification,
        "advance_allowed": advance,
        "success_claim_allowed": success_allowed,
        "evidence_complete": evidence_ok,
        "metric_recalculation_passed": metric_ok,
        "risk_budget_passed": risk_ok,
        "global_slot_passed": slot_ok,
        "partial_entry_protection_passed": protection_ok,
        "partial_entry_expiry_records": partial_expiry_records,
        "no_liquidation_passed": no_liquidation,
        "engine_errors_absent": engine_ok,
        "reasons": reasons,
        "recalculated": recalculated,
        "metrics": metrics,
        "evidence_files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--week", choices=("W1", "W2", "W3", "LONG"), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.result_dir, args.week)
    output = args.output or args.result_dir / "audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    lines = ["# Candidate 11 evidence audit", "", f"**{result['classification']}**", ""]
    for key in ("advance_allowed", "success_claim_allowed", "evidence_complete", "metric_recalculation_passed", "risk_budget_passed", "global_slot_passed", "partial_entry_protection_passed", "no_liquidation_passed"):
        lines.append(f"- {key}: `{result[key]}`")
    lines.extend(("", "## Reasons"))
    lines.extend(f"- {reason}" for reason in result["reasons"])
    output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 2 if result["classification"] == "IMPLEMENTATION_OR_EVIDENCE_FAILURE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
