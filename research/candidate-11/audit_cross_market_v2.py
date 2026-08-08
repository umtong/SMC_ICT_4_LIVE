#!/usr/bin/env python3
"""Independently audit cross-market Nautilus evidence without simulating PnL."""
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
    if not text or text.lower() in {"none", "null", "nan", "nat"}:
        raise InvalidOperation(f"invalid number {value!r}")
    return Decimal(text.split()[0])


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def plans(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [value for value in payload if isinstance(value, dict)]
    if isinstance(payload, dict):
        for key in ("plans", "submitted_plans", "records"):
            if isinstance(payload.get(key), list):
                return [value for value in payload[key] if isinstance(value, dict)]
    return []


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


def pick_timestamp(row: dict[str, str], names: tuple[str, ...]) -> Decimal | None:
    for name in names:
        value = timestamp_ns(row.get(name))
        if value is not None:
            return value
    return None


def audit(root: Path, week: str) -> dict[str, Any]:
    evidence_files = {
        name: {
            "exists": (root / name).is_file(),
            "bytes": (root / name).stat().st_size if (root / name).is_file() else 0,
        }
        for name in REQUIRED
    }
    reasons = [
        f"missing or empty evidence: {name}"
        for name, record in evidence_files.items()
        if not record["exists"] or record["bytes"] == 0
    ]
    metrics = load_json(root / "metrics.json") if (root / "metrics.json").is_file() else {}
    if not isinstance(metrics, dict):
        metrics = {}
        reasons.append("metrics.json is not an object")

    metric_ok = True
    recalculated: dict[str, Any] = {}
    try:
        starting = dec(metrics.get("starting_nav"))
        final = dec(metrics.get("final_nav"))
        days = dec(metrics.get("evaluation_calendar_days"))
        growth = float((final / starting) ** (Decimal(1) / days) - Decimal(1))
        net_return = float(final / starting - Decimal(1))
        recalculated = {
            "starting_nav": str(starting),
            "final_nav": str(final),
            "evaluation_calendar_days": float(days),
            "daily_geometric_growth": growth,
            "net_return": net_return,
        }
        if abs(float(metrics.get("daily_geometric_growth")) - growth) > 1e-10:
            metric_ok = False
            reasons.append("daily geometric growth mismatch")
        if abs(float(metrics.get("net_return")) - net_return) > 1e-10:
            metric_ok = False
            reasons.append("net return mismatch")
    except (InvalidOperation, ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        metric_ok = False
        reasons.append(f"NAV metrics cannot be independently recomputed: {exc}")

    plan_payload = load_json(root / "submitted_plans.json") if (root / "submitted_plans.json").is_file() else {}
    plan_rows = plans(plan_payload)
    risk_ok = True
    geometry_ok = True
    scenario_ids: set[str] = set()
    for index, plan in enumerate(plan_rows):
        scenario_id = str(plan.get("scenario_id") or "")
        if not scenario_id or scenario_id in scenario_ids:
            geometry_ok = False
            reasons.append(f"plan[{index}] has missing or duplicate scenario_id")
        scenario_ids.add(scenario_id)
        try:
            nav = dec(plan.get("nav_before"))
            budget = dec(plan.get("planned_loss_budget"))
            expected = dec(plan.get("expected_total_loss"))
            exact = nav * Decimal("0.03")
            tolerance = max(Decimal("0.01"), abs(exact) * Decimal("0.000001"))
            if abs(budget - exact) > tolerance or expected > budget + tolerance:
                risk_ok = False
                reasons.append(f"plan[{index}] violates exact 3% NAV loss budget")
        except InvalidOperation as exc:
            risk_ok = False
            reasons.append(f"plan[{index}] lacks risk evidence: {exc}")
        try:
            entry = dec(plan.get("entry"))
            stop = dec(plan.get("stop"))
            target = dec(plan.get("target"))
            direction = str(plan.get("direction"))
            valid = stop < entry < target if direction == "LONG" else target < entry < stop
            if direction not in {"LONG", "SHORT"} or not valid:
                geometry_ok = False
                reasons.append(f"plan[{index}] violates directional price order")
            if dec(plan.get("net_r")) < Decimal("1.25"):
                geometry_ok = False
                reasons.append(f"plan[{index}] has post-cost R below 1.25")
            details = plan.get("details") if isinstance(plan.get("details"), dict) else {}
            if details.get("target_method") != "FROZEN_BETA_IMPLIED_PEER_EQUILIBRIUM":
                geometry_ok = False
                reasons.append(f"plan[{index}] target method is not frozen peer equilibrium")
        except InvalidOperation as exc:
            geometry_ok = False
            reasons.append(f"plan[{index}] lacks geometric evidence: {exc}")

    position_rows = rows(root / "positions.csv")
    intervals: list[tuple[Decimal, Decimal]] = []
    for row in position_rows:
        opened = pick_timestamp(row, ("ts_opened", "ts_open", "open_time", "entry_time", "ts_init"))
        closed = pick_timestamp(row, ("ts_closed", "ts_close", "close_time", "exit_time", "ts_last"))
        if opened is not None and closed is not None and closed >= opened:
            intervals.append((opened, closed))
    intervals.sort()
    global_slot_ok = all(
        next_open >= prior_close
        for (_, prior_close), (next_open, _) in zip(intervals, intervals[1:])
    )
    if not global_slot_ok:
        reasons.append("overlapping position intervals violate global one-position invariant")

    order_rows = rows(root / "orders.csv")
    partial_records: list[dict[str, Any]] = []
    partial_ok = True
    maximum_delay = Decimal("60000000000")
    for order in order_rows:
        try:
            quantity = dec(order.get("quantity"))
            filled = dec(order.get("filled_qty"))
        except InvalidOperation:
            continue
        status = str(order.get("status", "")).upper()
        tif = str(order.get("time_in_force", "")).upper()
        tags = str(order.get("tags", "")).upper()
        partial_expiry = (
            status == "EXPIRED" and tif == "GTD"
            and Decimal(0) < filled < quantity
            and ("ENTRY" in tags or not tags)
        )
        if not partial_expiry:
            continue
        expired = pick_timestamp(order, ("ts_last", "expire_time_ns", "expire_time"))
        matching: list[tuple[Decimal, dict[str, str]]] = []
        if expired is not None:
            for position in position_rows:
                if position.get("instrument_id") != order.get("instrument_id"):
                    continue
                opened = pick_timestamp(position, ("ts_opened", "ts_init"))
                closed = pick_timestamp(position, ("ts_closed", "ts_last"))
                if opened is not None and closed is not None and opened <= expired <= closed:
                    matching.append((opened, position))
        position = max(matching, key=lambda value: value[0])[1] if matching else None
        closed = None if position is None else pick_timestamp(position, ("ts_closed", "ts_last"))
        delay = None if expired is None or closed is None else closed - expired
        passed = delay is not None and Decimal(0) <= delay <= maximum_delay
        partial_records.append({
            "instrument_id": order.get("instrument_id"),
            "quantity": str(quantity),
            "filled_qty": str(filled),
            "expired_ns": None if expired is None else str(expired),
            "closed_ns": None if closed is None else str(closed),
            "close_delay_ns": None if delay is None else str(delay),
            "fail_closed_within_one_bar": passed,
        })
        if not passed:
            partial_ok = False
            reasons.append("partially filled GTD entry remained open beyond one minute")

    event_ok = False
    event_count = 0
    event_path = root / "scenario_events.jsonl"
    if event_path.is_file():
        for line_number, line in enumerate(event_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                reasons.append(f"invalid event JSON at line {line_number}: {exc}")
                continue
            if isinstance(value, dict) and value.get("type"):
                event_count += 1
        event_ok = event_count > 0
    if not event_ok:
        reasons.append("cross-market event ledger is empty or invalid")

    no_liquidation = metrics.get("liquidation_detected") not in (True, "true", "True", 1)
    if not no_liquidation:
        reasons.append("liquidation detected")
    engine_errors = metrics.get("engine_errors")
    engine_ok = not (isinstance(engine_errors, list) and engine_errors)
    if not engine_ok:
        reasons.append(f"engine or order errors present: {engine_errors}")
    metric_overlap = int(metrics.get("global_slot_overlap_count") or 0) == 0
    if not metric_overlap:
        reasons.append("metrics reported global slot overlap")

    implementation = all((
        not any(not record["exists"] or record["bytes"] == 0 for record in evidence_files.values()),
        metric_ok, risk_ok, geometry_ok, global_slot_ok, metric_overlap,
        partial_ok, event_ok, no_liquidation, engine_ok,
    ))
    closed_trades = int(metrics.get("closed_trades") or 0)
    win_rate = float(metrics.get("win_rate") or 0.0)
    payoff = metrics.get("payoff_ratio")
    all_won = metrics.get("all_closed_trades_won") is True
    screening = (
        implementation
        and closed_trades >= 10
        and win_rate >= 0.80
        and (all_won or (payoff is not None and float(payoff) >= 1.20))
        and float(metrics.get("daily_geometric_growth") or 0.0) >= 0.01
        and float(metrics.get("closed_trade_max_drawdown") or 0.0) <= 0.20
    )
    if not implementation:
        classification = "IMPLEMENTATION_OR_EVIDENCE_FAILURE"
    elif not plan_rows:
        classification = "LOGIC_FAILURE_NO_EXECUTABLE_PLANS"
        reasons.append("causal detector produced no executable plans")
    elif closed_trades == 0:
        classification = "EXECUTION_FAILURE_NO_CLOSED_TRADES"
        reasons.append("plans were submitted but Nautilus reported no closed trades")
    elif screening:
        classification = "SCREENING_GATE_PASSED"
    else:
        classification = "ALPHA_OR_FREQUENCY_GATE_FAILED"
        reasons.append("frozen first-week screening gate did not pass")

    return {
        "schema": "candidate-11-cross-market-audit-v2",
        "week": week,
        "classification": classification,
        "implementation_passed": implementation,
        "screening_gate_passed": screening,
        "evidence_complete": not any(
            not record["exists"] or record["bytes"] == 0
            for record in evidence_files.values()
        ),
        "metric_recalculation_passed": metric_ok,
        "risk_budget_passed": risk_ok,
        "price_geometry_passed": geometry_ok,
        "global_slot_passed": global_slot_ok and metric_overlap,
        "partial_entry_protection_passed": partial_ok,
        "partial_entry_expiry_records": partial_records,
        "event_log_passed": event_ok,
        "event_count": event_count,
        "no_liquidation_passed": no_liquidation,
        "engine_errors_absent": engine_ok,
        "reasons": reasons,
        "recalculated": recalculated,
        "metrics": metrics,
        "evidence_files": evidence_files,
        "success_claim": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--week", choices=("C1", "C2", "C3", "C4", "C5", "C6"), required=True)
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
