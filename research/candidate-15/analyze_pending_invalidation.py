#!/usr/bin/env python3
"""Diagnose passive-parent entry/stop first passage from raw one-minute bars.

Diagnostic only: reads the exact archives used by Nautilus and never changes
orders or strategy state. Completed-bar first passage is classified conservatively;
a bar which touches both entry and stop remains unresolved at one-minute granularity.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import re
from zipfile import ZipFile

import pandas as pd

COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)
MINUTE = pd.Timedelta(minutes=1)


def read_object(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain an object")
    return payload


def load_symbol(root: Path, symbol: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted((root / "data" / symbol).glob("*.zip")):
        with ZipFile(path) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if len(names) != 1:
                raise ValueError(f"{path} must contain one CSV")
            raw = archive.read(names[0])
        frame = pd.read_csv(BytesIO(raw), header=None, names=COLUMNS)
        numeric = pd.to_numeric(frame["open_time"], errors="coerce")
        frame = frame.loc[numeric.notna()].copy()
        frame["open_time"] = numeric[numeric.notna()].astype("int64")
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"no archives for {symbol} in {root / 'data'}")
    raw = pd.concat(frames, ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
    first = int(raw["open_time"].iloc[0])
    unit = "ms" if first < 10**15 else "us"
    index = pd.to_datetime(raw["open_time"], unit=unit, utc=True) + MINUTE
    result = pd.DataFrame(index=index)
    for name in ("open", "high", "low", "close"):
        result[name] = pd.to_numeric(raw[name], errors="raise").to_numpy()
    return result[~result.index.duplicated(keep="last")].sort_index()


def order_type(event: dict) -> str | None:
    match = re.search(r"order_type=([A-Z_]+)", str(event.get("event", "")))
    return match.group(1) if match else None


def first_index(mask: pd.Series) -> pd.Timestamp | None:
    selected = mask.index[mask]
    return selected[0] if len(selected) else None


def bar_dict(frame: pd.DataFrame, ts: pd.Timestamp | None) -> dict | None:
    if ts is None or ts not in frame.index:
        return None
    row = frame.loc[ts]
    return {key: float(row[key]) for key in ("open", "high", "low", "close")}


def run(root: Path) -> dict:
    plans = read_object(root / "submitted_plans.json").get("plans", [])
    lifecycle = read_object(root / "order_lifecycle.json").get("events", [])
    if not isinstance(plans, list) or not isinstance(lifecycle, list):
        raise TypeError("plans and lifecycle events must be lists")

    submits = [event for event in lifecycle if event.get("type") == "GLOBAL_ENTRY_SUBMITTED"]
    plan_by_id = {str(plan["scenario_id"]): plan for plan in plans}
    frames: dict[str, pd.DataFrame] = {}
    records: list[dict] = []

    for index, submit in enumerate(submits):
        scenario_id = str(submit["scenario_id"])
        plan = plan_by_id[scenario_id]
        symbol = str(plan["symbol"])
        frames.setdefault(symbol, load_symbol(root, symbol))
        frame = frames[symbol]
        submit_ns = int(submit["ts_event"])
        next_submit_ns = (
            int(submits[index + 1]["ts_event"])
            if index + 1 < len(submits)
            else submit_ns + 70 * 60_000_000_000
        )
        window_events = [
            event for event in lifecycle
            if submit_ns <= int(event.get("ts_event", -1)) < next_submit_ns
        ]
        parent_fills = [
            event for event in window_events
            if event.get("type") == "ORDER_FILLED" and order_type(event) == "LIMIT"
        ]
        fill_ns = int(parent_fills[0]["ts_event"]) if parent_fills else None
        terminal_candidates = [
            int(event["ts_event"]) for event in window_events
            if event.get("type") in {"ORDER_EXPIRED", "ORDER_CANCELED"}
        ]
        end_ns = fill_ns or (min(terminal_candidates) if terminal_candidates else next_submit_ns - 1)
        submit_ts = pd.Timestamp(submit_ns, unit="ns", tz="UTC")
        end_ts = pd.Timestamp(end_ns, unit="ns", tz="UTC")
        path = frame.loc[(frame.index > submit_ts) & (frame.index <= end_ts)]

        direction = str(plan["direction"])
        entry = float(plan["entry"])
        stop = float(plan["stop"])
        if direction == "LONG":
            entry_mask = path["low"] <= entry
            stop_mask = path["low"] <= stop
        elif direction == "SHORT":
            entry_mask = path["high"] >= entry
            stop_mask = path["high"] >= stop
        else:
            raise ValueError(f"unknown direction {direction}")
        first_entry = first_index(entry_mask)
        first_stop = first_index(stop_mask)

        if first_stop is None:
            classification = "NO_STOP_TOUCH_BEFORE_TERMINAL"
        elif first_entry is None or first_stop < first_entry:
            classification = "STOP_BEFORE_FIRST_ENTRY_TOUCH"
        elif first_stop == first_entry:
            classification = "ENTRY_AND_STOP_SAME_FIRST_TOUCH_BAR_UNRESOLVED"
        elif fill_ns is not None and first_stop < pd.Timestamp(fill_ns, unit="ns", tz="UTC"):
            classification = "STOP_AFTER_ENTRY_TOUCH_BEFORE_ACTUAL_FILL"
        else:
            classification = "ENTRY_FIRST_STOP_NOT_BEFORE_FILL"

        transfer = plan.get("details", {}).get("candidate15_v8_transfer", {})
        records.append({
            "scenario_id": scenario_id,
            "symbol": symbol,
            "direction": direction,
            "stage": transfer.get("stage"),
            "entry": entry,
            "stop": stop,
            "submit_ts_ns": submit_ns,
            "fill_ts_ns": fill_ns,
            "terminal_ts_ns": end_ns,
            "fill_lag_minutes": None if fill_ns is None else (fill_ns - submit_ns) / 60_000_000_000,
            "first_entry_touch_ts_ns": None if first_entry is None else int(first_entry.value),
            "first_stop_touch_ts_ns": None if first_stop is None else int(first_stop.value),
            "first_entry_touch_bar": bar_dict(frame, first_entry),
            "first_stop_touch_bar": bar_dict(frame, first_stop),
            "classification": classification,
            "filled": fill_ns is not None,
        })

    counts: dict[str, int] = {}
    filled_counts: dict[str, int] = {}
    for record in records:
        label = record["classification"]
        counts[label] = counts.get(label, 0) + 1
        if record["filled"]:
            filled_counts[label] = filled_counts.get(label, 0) + 1
    output = {
        "schema": "candidate-15-pending-first-passage-diagnostic-v1",
        "diagnostic_only": True,
        "does_not_modify_execution": True,
        "submitted_plans": len(records),
        "filled_plans": sum(bool(record["filled"]) for record in records),
        "classification_counts": dict(sorted(counts.items())),
        "filled_classification_counts": dict(sorted(filled_counts.items())),
        "records": records,
    }
    (root / "pending_path_diagnostics.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in output.items() if key != "records"}, indent=2))
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    run(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
