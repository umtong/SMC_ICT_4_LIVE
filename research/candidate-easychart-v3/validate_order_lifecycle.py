"""Reject causal-trade lifecycle violations in EasyChart v3 artifacts."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            text = line.strip()
            if text:
                events.append(json.loads(text))
    return events


def validate_lifecycle(
    events: Iterable[dict[str, Any]],
    trade_audit: pd.DataFrame,
) -> dict[str, Any]:
    """Return deterministic lifecycle diagnostics.

    A single planned entry may fill in pieces before its exit, but once a stop
    or target starts executing the parent must never fill again.  One plan may
    also not create a second position after the first position has closed.
    """
    event_list = list(events)
    submitted_by_plan: dict[str, dict[str, Any]] = {}
    entry_to_plan: dict[str, str] = {}
    for event in event_list:
        if event.get("kind") != "submitted":
            continue
        plan_id = event.get("plan_id")
        entry_id = event.get("entry_client_order_id")
        if plan_id and entry_id:
            submitted_by_plan[str(plan_id)] = event
            entry_to_plan[str(entry_id)] = str(plan_id)

    exit_seen: set[str] = set()
    entry_fill_after_exit: list[dict[str, Any]] = []
    misclassified_entry_fills: list[dict[str, Any]] = []
    unresolved_fill_events: list[dict[str, Any]] = []
    fill_counts: Counter[str] = Counter()

    for sequence, event in enumerate(event_list):
        if event.get("kind") != "order_filled":
            continue
        client_order_id = str(event.get("client_order_id"))
        direct_plan = event.get("plan_id")
        plan_id = str(direct_plan) if direct_plan else entry_to_plan.get(client_order_id)
        if not plan_id:
            unresolved_fill_events.append(
                {
                    "sequence": sequence,
                    "client_order_id": client_order_id,
                    "event_ts_ns": event.get("event_ts_ns"),
                    "role": event.get("role"),
                },
            )
            continue
        fill_counts[plan_id] += 1
        is_entry = entry_to_plan.get(client_order_id) == plan_id
        if is_entry:
            if event.get("role") != "ENTRY":
                misclassified_entry_fills.append(
                    {
                        "plan_id": plan_id,
                        "sequence": sequence,
                        "client_order_id": client_order_id,
                        "event_ts_ns": event.get("event_ts_ns"),
                        "reported_role": event.get("role"),
                    },
                )
            if plan_id in exit_seen:
                entry_fill_after_exit.append(
                    {
                        "plan_id": plan_id,
                        "sequence": sequence,
                        "client_order_id": client_order_id,
                        "event_ts_ns": event.get("event_ts_ns"),
                        "last_qty": event.get("last_qty"),
                        "last_px": event.get("last_px"),
                    },
                )
        else:
            exit_seen.add(plan_id)

    duplicate_plan_positions: dict[str, int] = {}
    if not trade_audit.empty and "plan_id" in trade_audit.columns:
        counts = trade_audit.dropna(subset=["plan_id"]).groupby("plan_id", sort=True).size()
        duplicate_plan_positions = {
            str(plan_id): int(count)
            for plan_id, count in counts.items()
            if int(count) > 1
        }

    failures: list[str] = []
    if entry_fill_after_exit:
        failures.append(f"{len(entry_fill_after_exit)} entry fills occurred after an exit had started")
    if misclassified_entry_fills:
        failures.append(f"{len(misclassified_entry_fills)} known parent fills lost their ENTRY identity")
    if duplicate_plan_positions:
        failures.append(
            f"{len(duplicate_plan_positions)} plans created more than one position lifecycle",
        )

    return {
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
        "submitted_plans": len(submitted_by_plan),
        "plans_with_fill_events": len(fill_counts),
        "entry_fill_after_exit": entry_fill_after_exit,
        "misclassified_entry_fills": misclassified_entry_fills,
        "duplicate_plan_positions": duplicate_plan_positions,
        # Unresolved fills are diagnostic only because evaluation-end flatten
        # and emergency orders may intentionally have no plan tag.
        "unresolved_fill_events": unresolved_fill_events,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    events_path = args.output / "scenario_events.jsonl"
    audit_path = args.output / "trade_audit.csv"
    if not events_path.exists() or not audit_path.exists():
        raise SystemExit("required backtest evidence is missing")
    result = validate_lifecycle(_read_events(events_path), pd.read_csv(audit_path))
    path = args.output / "order_lifecycle_validation.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit("; ".join(result["failures"]))


if __name__ == "__main__":
    main()
