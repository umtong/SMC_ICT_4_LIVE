#!/usr/bin/env python3
"""Execution-evidence robust wrapper for the v46 model trainer."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

import train_v46_evidence_model as _base


_BASE_EVENT_RECORDS = _base._event_records


def _column(frame: pd.DataFrame, *needles: str) -> str | None:
    lowered = {str(column).lower(): str(column) for column in frame.columns}
    for needle in needles:
        if needle.lower() in lowered:
            return lowered[needle.lower()]
    for needle in needles:
        matches = [column for low, column in lowered.items() if needle.lower() in low]
        if matches:
            return matches[0]
    return None


def _as_ns(value: Any) -> int:
    number = _base._number(value)
    if math.isfinite(number):
        integer = int(number)
        if integer < 10_000_000_000:
            return integer * 1_000_000_000
        if integer < 10_000_000_000_000:
            return integer * 1_000_000
        if integer < 10_000_000_000_000_000:
            return integer * 1_000
        return integer
    try:
        return int(pd.Timestamp(value, tz="UTC").value)
    except Exception:
        return 0


def _scenario_events(parent: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in parent.rglob("scenario_events.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            flat = _base._flatten(event)
            ts = _base._timestamp(flat)
            if ts <= 0:
                continue
            text = " ".join(str(value) for value in flat.values()).upper()
            if not any(
                token in text
                for token in (
                    "POSITION_OPEN",
                    "ENTRY_FILLED",
                    "ENTRY_SUBMITTED",
                    "ORDER_FILLED",
                    "POSITION_OPENED",
                )
            ):
                continue
            events.append({"ts": ts, "flat": flat})
    events.sort(key=lambda item: int(item["ts"]))
    return events


def _position_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in root.rglob("positions.csv"):
        try:
            frame = pd.read_csv(path)
        except Exception:
            continue
        if frame.empty:
            continue
        pnl_column = _column(frame, "realized_pnl", "realised_pnl", "net_pnl", "pnl")
        open_column = _column(frame, "ts_opened", "opened_ts", "entry_ts", "open_time")
        close_column = _column(frame, "ts_closed", "closed_ts", "exit_ts", "close_time")
        side_column = _column(frame, "side", "position_side")
        if pnl_column is None or open_column is None or side_column is None:
            continue
        events = _scenario_events(path.parent)
        for _, row in frame.iterrows():
            pnl = _base._number(row[pnl_column])
            opened = _as_ns(row[open_column])
            if not math.isfinite(pnl) or opened <= 0:
                continue
            nearest: dict[str, Any] | None = None
            distance = 10**30
            for event in events:
                delta = opened - int(event["ts"])
                if -60_000_000_000 <= delta <= 600_000_000_000 and abs(delta) < distance:
                    nearest = event
                    distance = abs(delta)
            flat = dict(nearest["flat"]) if nearest is not None else {}
            flat.update(
                {
                    "position.side": row[side_column],
                    "position.realized_pnl": pnl,
                    "position.ts_opened": opened,
                    "position.ts_closed": _as_ns(row[close_column]) if close_column else opened,
                    "position.position_id": row.get(_column(frame, "position_id") or "", f"{path.name}-{opened}"),
                },
            )
            records.append(flat)
    return records


def _event_records(root: Path) -> list[dict[str, Any]]:
    records = _BASE_EVENT_RECORDS(root)
    position_records = _position_records(root)
    by_key: dict[str, dict[str, Any]] = {}
    for index, flat in enumerate(records + position_records):
        scenario = str(
            _base._find(flat, "scenario_id", "trade_id", "position_id")
            or f"record-{index}-{_base._timestamp(flat)}"
        )
        current = by_key.get(scenario)
        if current is None or len(flat) > len(current):
            by_key[scenario] = flat
    return list(by_key.values())


_base._event_records = _event_records


if __name__ == "__main__":
    _base.main()
