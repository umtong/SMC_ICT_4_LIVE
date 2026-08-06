#!/usr/bin/env python3
"""Derive V17 dual-inventory auction states.

The candidate combines two independent causal event families:

* OI contraction/deleveraging: retain V13's full opposite-range CHoCH reversal
  and full event-range measured acceptance continuation; midpoint-only failures
  are NO_TRADE.
* OI expansion/new positioning: two non-overlapping five-minute price/OI
  expansions break a prior 30-minute external boundary, futures and spot
  aggressive flow agree, and the next completed minute holds outside.

This module emits schedules only. NautilusTrader remains the sole order, fill,
fee, funding, position, margin, accounting, and NAV engine.
"""
from __future__ import annotations

import argparse
import json
import math
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any

from derive_nt_lvcfr_v13_signals import (
    FIRST_BREAK_CHOCH_REVERSAL,
    MEASURED_ACCEPTANCE_CONTINUATION,
    derive_v13,
)
from nt_lvcfr_data import (
    FIVE_MINUTES_NS,
    NS_PER_MINUTE,
    _atr,
    _five,
    load_kline_minutes,
    load_open_interest,
)

SPOT_LED_OI_EXPANSION_ACCEPTANCE = "SPOT_LED_OI_EXPANSION_ACCEPTANCE"


def derive_expansion_signals(
    *,
    raw_root: Path,
    evaluation_start_ns: int,
    evaluation_end_ns: int,
    local_range_minutes: int = 30,
    waypoint_minutes: int = 240,
    first_displacement_bp: float = 12.0,
    total_oi_increase_bp: float = 10.0,
    activity_baseline_5m: int = 72,
    activity_min_periods: int = 24,
    second_activity_min: float = 0.70,
    stop_buffer_atr: float = 0.20,
    atr_minutes: int = 60,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if min(local_range_minutes, waypoint_minutes, activity_baseline_5m, activity_min_periods, atr_minutes) <= 0:
        raise ValueError("all windows must be positive")
    if first_displacement_bp <= 0.0 or total_oi_increase_bp <= 0.0:
        raise ValueError("expansion thresholds must be positive")
    if stop_buffer_atr < 0.0:
        raise ValueError("stop_buffer_atr must be non-negative")

    futures = load_kline_minutes(sorted((raw_root / "futures_kline").glob("*.zip")))
    spot = load_kline_minutes(sorted((raw_root / "spot_kline").glob("*.zip")))
    oi = load_open_interest(sorted((raw_root / "open_interest").glob("*.zip")))
    futures_by_minute = {bar.minute_index: bar for bar in futures}
    spot_by_minute = {bar.minute_index: bar for bar in spot}
    atr = _atr(futures, atr_minutes)
    futures_five = _five(futures)
    spot_five = _five(spot)
    aligned = sorted(set(futures_five) & set(spot_five) & set(oi))

    signals: list[dict[str, Any]] = []
    counters = {
        "aligned_five_minute_points": len(aligned),
        "overlapping_event_windows_skipped": 0,
        "emitted_expansion_signals": 0,
    }
    last_event_end_ns: int | None = None

    for index in range(2, len(aligned)):
        second_end = aligned[index]
        first_end = aligned[index - 1]
        before_end = aligned[index - 2]
        if not evaluation_start_ns <= second_end < evaluation_end_ns:
            continue
        if second_end - first_end != FIVE_MINUTES_NS or first_end - before_end != FIVE_MINUTES_NS:
            continue
        event_start_ns = first_end - FIVE_MINUTES_NS
        if last_event_end_ns is not None and event_start_ns < last_event_end_ns:
            counters["overlapping_event_windows_skipped"] += 1
            continue

        baseline_times = aligned[max(0, index - activity_baseline_5m) : index]
        baseline = [futures_five[timestamp].notional for timestamp in baseline_times]
        if len(baseline) < activity_min_periods:
            continue
        baseline_notional = median(baseline)
        if baseline_notional <= 0.0:
            continue

        first = futures_five[first_end]
        second = futures_five[second_end]
        second_spot = spot_five[second_end]
        direction = 1 if first.return_bp > 0.0 else (-1 if first.return_bp < 0.0 else 0)
        if direction == 0:
            continue
        first_displacement = direction * first.return_bp
        second_progress = direction * (second.close / first.close - 1.0) * 10_000.0
        oi_before, oi_first, oi_second = oi[before_end], oi[first_end], oi[second_end]
        first_oi_increase = (oi_first / oi_before - 1.0) * 10_000.0
        second_oi_increase = (oi_second / oi_first - 1.0) * 10_000.0
        total_oi_increase = (oi_second / oi_before - 1.0) * 10_000.0
        activity_ratio = second.notional / baseline_notional
        futures_flow = direction * second.flow
        spot_flow = direction * second_spot.flow
        end_minute = second_end // NS_PER_MINUTE
        at = atr.get(end_minute - 1)
        values = (
            first_displacement,
            second_progress,
            first_oi_increase,
            second_oi_increase,
            total_oi_increase,
            activity_ratio,
            futures_flow,
            spot_flow,
            at or float("nan"),
        )
        if at is None or at <= 0.0 or not all(math.isfinite(value) for value in values):
            continue
        if not (
            first_displacement >= first_displacement_bp
            and second_progress > 0.0
            and first_oi_increase > 0.0
            and second_oi_increase > 0.0
            and total_oi_increase >= total_oi_increase_bp
            and activity_ratio >= second_activity_min
            and futures_flow > 0.0
            and spot_flow > 0.0
        ):
            continue

        event_start_minute = end_minute - 10
        local_rows = [
            futures_by_minute.get(minute)
            for minute in range(event_start_minute - local_range_minutes, event_start_minute)
        ]
        event_rows = [futures_by_minute.get(minute) for minute in range(event_start_minute, end_minute)]
        waypoint_rows = [
            futures_by_minute.get(minute)
            for minute in range(event_start_minute - waypoint_minutes, event_start_minute)
        ]
        hold = futures_by_minute.get(end_minute)
        hold_spot = spot_by_minute.get(end_minute)
        if (
            any(row is None for row in local_rows)
            or any(row is None for row in event_rows)
            or any(row is None for row in waypoint_rows)
            or hold is None
            or hold_spot is None
        ):
            continue
        local = [row for row in local_rows if row is not None]
        event = [row for row in event_rows if row is not None]
        prior = [row for row in waypoint_rows if row is not None]
        boundary = max(row.high for row in local) if direction > 0 else min(row.low for row in local)
        hold_futures_flow = direction * hold.flow
        hold_spot_flow = direction * hold_spot.flow
        if not (
            direction * (second.close - boundary) > 0.0
            and direction * (hold.close - boundary) > 0.0
            and hold_futures_flow > 0.0
            and hold_spot_flow > 0.0
        ):
            continue

        stop = boundary - direction * stop_buffer_atr * at
        if direction * (hold.close - stop) <= 0.0:
            continue
        confirm_ns = (end_minute + 1) * NS_PER_MINUTE
        waypoint = max(row.high for row in prior) if direction > 0 else min(row.low for row in prior)
        waypoint_ahead = direction * (waypoint - hold.close) > 0.0
        scenario_id = "NT-LVCFR-V17-SPOT-OI-EXPANSION-" + sha256(
            f"{second_end}|{direction}|{boundary:.12g}|{hold.close:.12g}".encode()
        ).hexdigest()[:16]
        details = {
            "scenario_kind": SPOT_LED_OI_EXPANSION_ACCEPTANCE,
            "entry_kind": "CONTINUATION",
            "routed_direction": direction,
            "event_start_minute": event_start_minute,
            "event_end_minute": end_minute,
            "local_range_minutes": local_range_minutes,
            "local_range_low": min(row.low for row in local),
            "local_range_high": max(row.high for row in local),
            "broken_external_boundary": boundary,
            "event_low": min(row.low for row in event),
            "event_high": max(row.high for row in event),
            "hold_minute": end_minute,
            "hold_close": hold.close,
            "first_displacement_bp": first_displacement,
            "second_progress_bp": second_progress,
            "first_oi_increase_bp": first_oi_increase,
            "second_oi_increase_bp": second_oi_increase,
            "total_oi_increase_bp": total_oi_increase,
            "second_activity_ratio": activity_ratio,
            "second_directional_futures_flow": futures_flow,
            "second_directional_spot_flow": spot_flow,
            "hold_directional_futures_flow": hold_futures_flow,
            "hold_directional_spot_flow": hold_spot_flow,
            "stop_buffer_atr": stop_buffer_atr,
            "structural_waypoint_kind": "PRIOR_240_MINUTE_EXTERNAL_WAYPOINT" if waypoint_ahead else None,
        }
        signal: dict[str, Any] = {
            "scenario_id": scenario_id,
            "scenario_kind": SPOT_LED_OI_EXPANSION_ACCEPTANCE,
            "entry_kind": "CONTINUATION",
            "confirm_time_ns": confirm_ns,
            "eligible_time_ns": confirm_ns,
            "direction": direction,
            "initial_stop": stop,
            "atr": at,
            "first_start_time_ns": event_start_minute * NS_PER_MINUTE,
            "first_end_time_ns": first_end,
            "target_mode": "EXISTING_NET_R_OBJECTIVE",
            "details": details,
        }
        if waypoint_ahead:
            signal["structural_protection_trigger"] = waypoint
            signal["target_mode"] = "STRUCTURAL_PROTECTION_THEN_EXISTING_OBJECTIVE"
        signals.append(signal)
        counters["emitted_expansion_signals"] += 1
        last_event_end_ns = second_end

    return signals, counters


def derive_v17(
    *,
    source_signals: Path,
    raw_root: Path,
    data_manifest_path: Path,
    output_signals: Path,
    output_manifest: Path,
) -> list[dict[str, Any]]:
    data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
    v13_signals_path = output_manifest.with_name(output_manifest.stem + "-v13-intermediate-signals.json")
    v13_manifest_path = output_manifest.with_name(output_manifest.stem + "-v13-intermediate.json")
    v13_all = derive_v13(
        source_signals=source_signals,
        raw_root=raw_root,
        output_signals=v13_signals_path,
        output_manifest=v13_manifest_path,
    )
    deleveraging = [
        signal
        for signal in v13_all
        if str(signal.get("scenario_kind")) in {FIRST_BREAK_CHOCH_REVERSAL, MEASURED_ACCEPTANCE_CONTINUATION}
    ]
    expansion, expansion_counters = derive_expansion_signals(
        raw_root=raw_root,
        evaluation_start_ns=int(data_manifest["evaluation_start_ns"]),
        evaluation_end_ns=int(data_manifest["evaluation_end_ns"]),
    )
    combined = sorted(
        [*deleveraging, *expansion],
        key=lambda item: (int(item["confirm_time_ns"]), str(item["scenario_id"])),
    )
    ids = [str(signal["scenario_id"]) for signal in combined]
    if len(set(ids)) != len(ids):
        raise RuntimeError("duplicate V17 scenario IDs")
    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(json.dumps(combined, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state_counts: dict[str, int] = {}
    for signal in combined:
        state = str(signal.get("scenario_kind"))
        state_counts[state] = state_counts.get(state, 0) + 1
    v13_manifest = json.loads(v13_manifest_path.read_text(encoding="utf-8"))
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v17-dual-inventory-auction",
        "engine_status": "causal_schedule_only_no_backtest",
        "source_contraction_event_count": v13_manifest["source_signal_count"],
        "retained_deleveraging_signal_count": len(deleveraging),
        "expansion_signal_count": len(expansion),
        "derived_signal_count": len(combined),
        "state_counts": dict(sorted(state_counts.items())),
        "expansion_counters": expansion_counters,
        "source_signals": str(source_signals),
        "output_signals": str(output_signals),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return combined


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v1.json"
    if not source.exists():
        source = prepared / "signals.json"
    combined = derive_v17(
        source_signals=source,
        raw_root=prepared / "raw",
        data_manifest_path=prepared / "data_manifest.json",
        output_signals=prepared / "signals.json",
        output_manifest=args.output_manifest.resolve(),
    )
    print(json.dumps({"derived_signals": len(combined)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
