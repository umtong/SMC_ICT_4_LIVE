#!/usr/bin/env python3
"""Trace every v59 accepted-unwind event through the frozen geometry contract."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import forced_unwind_geometry as geometry


def _contiguous(times: pd.DatetimeIndex) -> bool:
    return len(times) <= 1 or bool(np.all(np.diff(times.asi8) == 60_000_000_000))


def diagnose(args: argparse.Namespace) -> None:
    events = pd.read_csv(args.events)
    events["event_time"] = pd.to_datetime(events["event_time"], utc=True, format="mixed")
    events = events[
        events["period_label"].eq(args.period_label)
        & events["state"].eq("FORCED_UNWIND_ACCEPTED")
    ].copy()
    priority = {"public_vectorized_no_ema": 0, "impulse_only_2atr": 1}
    events["family_priority"] = events["family"].map(priority).fillna(9)
    events = events.sort_values(
        ["symbol", "event_time", "family_priority", "impulse_atr"],
        ascending=[True, True, True, False], kind="stable",
    ).drop_duplicates(["symbol", "event_time"], keep="first")

    load_start = date.fromisoformat(args.start) - timedelta(days=1)
    load_end = date.fromisoformat(args.end) + timedelta(days=1)
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "period_label": args.period_label,
        "accepted_episode_count": int(len(events)),
        "by_symbol": events["symbol"].value_counts().sort_index().to_dict(),
        "events": [],
    }
    for symbol, symbol_events in events.groupby("symbol", sort=True):
        minute, _, missing = geometry._load_observed_minutes(
            symbol=symbol, start=load_start, end=load_end,
            cache=Path(args.cache) / symbol,
            candidate05=Path(args.candidate05_path),
            candidate51=Path(args.candidate51_path),
        )
        times = pd.DatetimeIndex(pd.to_datetime(minute["close_time_dt"], utc=True))
        for _, event in symbol_events.iterrows():
            event_time = pd.Timestamp(event["event_time"])
            event_loc = int(times.searchsorted(event_time, side="right")) - 1
            event_payload: dict[str, Any] = {
                "symbol": symbol,
                "event_time": event_time.isoformat(),
                "event_loc": event_loc,
                "nearest_event_clock": None if event_loc < 0 else times[event_loc].isoformat(),
                "event_clock_delta_seconds": None if event_loc < 0 else (times[event_loc] - event_time).total_seconds(),
                "side": int(event["side"]),
                "missing_minutes_in_loaded_range": len(missing),
                "entry_modes": {},
            }
            for entry_mode in geometry.ENTRY_MODES:
                signal_time = event_time if entry_mode == "direct" else event_time + pd.Timedelta(minutes=15)
                entry_loc = int(times.searchsorted(signal_time, side="right"))
                mode: dict[str, Any] = {
                    "signal_time": signal_time.isoformat(),
                    "entry_loc": entry_loc,
                    "next_observed_time": None if entry_loc >= len(times) else times[entry_loc].isoformat(),
                    "next_delta_seconds": None if entry_loc >= len(times) else (times[entry_loc] - signal_time).total_seconds(),
                    "rejections": [],
                    "stops": {},
                    "valid_geometry_count": 0,
                }
                if entry_loc >= len(times):
                    mode["rejections"].append("entry_after_loaded_data")
                elif times[entry_loc] - signal_time != pd.Timedelta(minutes=1):
                    mode["rejections"].append("next_close_not_exactly_plus_60s")
                if event_loc < 60:
                    mode["rejections"].append("insufficient_impulse_context")
                if entry_loc < len(times) and event_loc >= 60:
                    acceptance_start = event_loc - 14
                    impulse_start = event_loc - 59
                    pre_impulse = impulse_start - 1
                    context_times = times[pre_impulse:entry_loc + 1]
                    mode["context_count"] = len(context_times)
                    mode["context_first"] = context_times[0].isoformat() if len(context_times) else None
                    mode["context_last"] = context_times[-1].isoformat() if len(context_times) else None
                    mode["context_contiguous"] = _contiguous(context_times)
                    if not mode["context_contiguous"]:
                        mode["rejections"].append("context_not_contiguous")
                    entry = float(minute.iloc[entry_loc]["open"])
                    origin = float(minute.iloc[pre_impulse]["close"])
                    acceptance = minute.iloc[acceptance_start:event_loc + 1]
                    for stop_mode in geometry.STOP_MODES:
                        stop = (
                            float(acceptance["low"].min()) if int(event["side"]) > 0
                            else float(acceptance["high"].max())
                        ) if stop_mode == "acceptance_extreme" else origin
                        valid = (0.0 < stop < entry) if int(event["side"]) > 0 else (stop > entry > 0.0)
                        mode["stops"][stop_mode] = {
                            "entry": entry, "stop": stop, "origin": origin,
                            "valid": bool(valid),
                        }
                        if not valid:
                            mode["rejections"].append(f"invalid_stop:{stop_mode}")
                    for hold in geometry.HOLDS_MIN:
                        end_time = times[entry_loc] + pd.Timedelta(minutes=hold)
                        end_loc = int(times.searchsorted(end_time, side="left"))
                        available = end_loc < len(times) and times[end_loc] == end_time
                        path = times[entry_loc:end_loc + 1] if available else pd.DatetimeIndex([])
                        mode[f"path_{hold}m"] = {
                            "target_time": end_time.isoformat(),
                            "available": bool(available),
                            "count": len(path),
                            "expected_count": hold + 1,
                            "contiguous": bool(available and _contiguous(path)),
                        }
                    for stop_mode in geometry.STOP_MODES:
                        for target_mode in geometry.TARGET_MODES:
                            for hold in geometry.HOLDS_MIN:
                                result = geometry._event_geometry(
                                    minute, event, entry_mode, stop_mode, target_mode, hold
                                )
                                if result is not None:
                                    mode["valid_geometry_count"] += 1
                                    rows.append({
                                        "symbol": symbol, "event_time": event_time,
                                        "entry_mode": entry_mode, "stop_mode": stop_mode,
                                        "target_mode": target_mode, "hold_min": hold,
                                        "outcome": result["outcome"],
                                        "r_multiple": result["r_multiple"],
                                    })
                mode["rejections"] = sorted(set(mode["rejections"]))
                event_payload["entry_modes"][entry_mode] = mode
            summary["events"].append(event_payload)

    summary["valid_geometry_records"] = len(rows)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "DIAGNOSIS.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    pd.DataFrame(rows).to_csv(output / "VALID_RECORDS.csv", index=False)
    print(json.dumps({"episodes": len(events), "valid_geometry_records": len(rows)}, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--period-label", required=True)
    root.add_argument("--start", required=True)
    root.add_argument("--end", required=True)
    root.add_argument("--events", default="research/candidate-51/evidence/derivatives-impulse-v57/EVENTS.csv")
    root.add_argument("--cache", default=".cache/candidate-51-v59-diagnosis")
    root.add_argument("--candidate05-path", default="research/candidate-05")
    root.add_argument("--candidate51-path", default="research/candidate-51")
    root.add_argument("--output", required=True)
    root.set_defaults(func=diagnose)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
