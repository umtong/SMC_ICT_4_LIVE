#!/usr/bin/env python3
"""Validate Candidate 18 v6 from native NautilusTrader reports."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import re
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _money(text: Any) -> float:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(text).replace(",", ""))
    return float(match.group()) if match else float("nan")


def _safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


def validate(root: Path, period: str) -> dict[str, Any]:
    metrics = _read_json(root / "metrics.json")
    diagnostics = _read_json(root / "strategy_diagnostics.json")
    ticks = _read_json(root / "trade_tick_manifest.json")
    events = [
        json.loads(line)
        for line in (root / "scenario_events.jsonl").read_text(
            encoding="utf-8",
        ).splitlines()
        if line.strip()
    ]
    with (root / "orders.csv").open(newline="", encoding="utf-8") as stream:
        orders = list(csv.DictReader(stream))
    with (root / "positions.csv").open(newline="", encoding="utf-8") as stream:
        positions = list(csv.DictReader(stream))

    entries = [
        row
        for row in orders
        if "CANDIDATE18_BOUNDED_GTD_ENTRY" in str(row.get("tags", ""))
    ]
    stops = [
        row
        for row in orders
        if "CANDIDATE18_TRADE_TICK_EMULATED_STOP"
        in str(row.get("tags", ""))
    ]
    targets = [
        row
        for row in orders
        if "CANDIDATE18_MANAGED_TARGET" in str(row.get("tags", ""))
    ]
    requested_qty = sum(float(row.get("quantity") or 0.0) for row in entries)
    filled_qty = sum(float(row.get("filled_qty") or 0.0) for row in entries)
    entry_fill_fractions = [
        float(row.get("filled_qty") or 0.0)
        / max(float(row.get("quantity") or 0.0), 1e-12)
        for row in entries
    ]

    submissions = [
        event for event in events if event.get("event_type") == "ENTRY_SUBMITTED"
    ]
    risks: list[float] = []
    malformed_risk: list[str | None] = []
    by_time: dict[int, dict[str, Any]] = {}
    for event in submissions:
        details = event.get("details", {})
        try:
            fraction = (
                float(details["planned_account_loss_at_worst_fill"])
                / float(details["equity"])
            )
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            malformed_risk.append(event.get("scenario_id"))
            continue
        if not math.isfinite(fraction) or fraction < 0.0:
            malformed_risk.append(event.get("scenario_id"))
            continue
        risks.append(fraction)
        by_time[int(event["event_time_ns"])] = event

    max_realized_loss = 0.0
    unmatched_positions: list[str | None] = []
    for position in positions:
        opened_ns = int(
            datetime.fromisoformat(position["ts_opened"]).timestamp()
            * 1_000_000_000
        )
        candidates = [
            event
            for ts, event in by_time.items()
            if 0 <= opened_ns - ts <= 120 * 1_000_000_000
        ]
        if not candidates:
            unmatched_positions.append(position.get("opening_order_id"))
            continue
        event = min(
            candidates,
            key=lambda item: opened_ns - int(item["event_time_ns"]),
        )
        pnl = _money(position.get("realized_pnl"))
        if math.isfinite(pnl) and pnl < 0.0:
            max_realized_loss = max(
                max_realized_loss,
                -pnl / float(event["details"]["equity"]),
            )

    future_reference = [
        event.get("event_id")
        for event in events
        if int(event.get("event_time_ns", 0))
        > int(event.get("observed_time_ns", 0))
    ]
    rejection_events = list(
        diagnostics.get("candidate18_v6_order_rejection_events", []),
    )
    stop_capacity = float(
        diagnostics.get("candidate18_v4_stop_qty_submitted", 0.0),
    )
    target_capacity = float(
        diagnostics.get("candidate18_v4_target_qty_submitted", 0.0),
    )
    checks = {
        "native_trade_ticks_loaded": int(ticks.get("trade_ticks", 0)) > 1_000,
        "metrics_record_trade_ticks": int(metrics.get("trade_ticks", 0))
        == int(ticks.get("trade_ticks", -1)),
        "bar_matching_disabled": metrics.get("bar_execution") is False,
        "trade_matching_enabled": metrics.get("trade_execution") is True,
        "bounded_gtd_entry_contract": bool(entries)
        and all(
            row.get("type") == "LIMIT"
            and row.get("time_in_force") == "GTD"
            for row in entries
        ),
        "has_filled_trade": int(metrics.get("trades", 0)) > 0,
        "fill_quantity_matches_diagnostics": math.isclose(
            float(
                diagnostics.get(
                    "candidate18_v6_filled_entry_qty",
                    0.0,
                ),
            ),
            filled_qty,
            rel_tol=0.0,
            abs_tol=1e-8,
        ),
        "stop_capacity_covers_fills": stop_capacity + 1e-8 >= filled_qty,
        "target_capacity_covers_fills": target_capacity + 1e-8 >= filled_qty,
        "protection_created": int(
            diagnostics.get("candidate18_v5_trade_tick_stop_batches", 0),
        )
        > 0,
        "no_entry_rejections": int(
            diagnostics.get("candidate18_v6_entry_rejections", 0),
        )
        == 0,
        "no_protective_rejections": int(
            diagnostics.get("candidate18_v4_protective_rejections", 0),
        )
        == 0,
        "no_order_rejections": int(diagnostics.get("order_rejections", 0))
        == 0,
        "rejection_log_empty": not rejection_events,
        "three_percent_planned_risk": not malformed_risk
        and max(risks, default=0.0) <= 0.0300000001,
        "realized_loss_contained": max_realized_loss <= 0.031,
        "positions_match_signals": not unmatched_positions,
        "no_future_reference": not future_reference,
        "single_entry_intent": int(
            diagnostics.get("max_simultaneous_entry_intents", 0),
        )
        <= 1,
        "single_position": int(
            diagnostics.get("max_open_positions_observed", 0),
        )
        <= 1,
        "no_liquidation": int(metrics.get("liquidations", 0)) == 0,
        "positive_equity": float(metrics.get("min_equity", 0.0)) > 0.0,
    }
    decision = {
        "schema": "candidate-18-v6-development-v1",
        "period": period,
        "integrity_pass": all(checks.values()),
        "checks": checks,
        "gate_pass": bool(metrics.get("gate_pass", False)),
        "entry_rows": len(entries),
        "stop_rows": len(stops),
        "target_rows": len(targets),
        "requested_entry_qty": requested_qty,
        "filled_entry_qty": filled_qty,
        "aggregate_fill_fraction": (
            filled_qty / requested_qty if requested_qty > 0.0 else 0.0
        ),
        "entry_fill_fraction_min": min(entry_fill_fractions, default=0.0),
        "entry_fill_fraction_median": (
            sorted(entry_fill_fractions)[len(entry_fill_fractions) // 2]
            if entry_fill_fractions
            else 0.0
        ),
        "entry_fill_fraction_max": max(entry_fill_fractions, default=0.0),
        "max_planned_risk_fraction": max(risks, default=0.0),
        "max_realized_loss_fraction": max_realized_loss,
        "malformed_risk": malformed_risk,
        "unmatched_positions": unmatched_positions,
        "future_reference": future_reference,
        "rejection_events": rejection_events,
        "metrics": metrics,
        "diagnostics": diagnostics,
    }
    return _safe(decision)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--period", required=True)
    args = parser.parse_args()
    decision = validate(args.root.resolve(), args.period)
    destination = args.root.resolve() / "v6_development_decision.json"
    destination.write_text(
        json.dumps(decision, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(decision, indent=2, sort_keys=True, allow_nan=False))
    if not decision["integrity_pass"]:
        raise SystemExit("Candidate 18 v6 execution integrity failed")


if __name__ == "__main__":
    main()
