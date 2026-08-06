#!/usr/bin/env python3
"""Resolve each liquidation event by the first completed post-event range break.

V12 is a new state-space candidate, not a V11 parameter patch. A ten-minute
liquidation/OI-contraction event defines a temporary auction range. The event's
preassigned direction is treated only as context. From the first completed
post-event minute, the router waits at most 120 completed minutes for the first
close beyond either event extreme:

* a close beyond the extreme in the original event direction is a causal BOS
  continuation;
* a close beyond the opposite extreme is a causal CHoCH reversal;
* no completed range break emits NO_TRADE.

The invalidation level is the event midpoint plus/minus the already frozen 0.20
ATR buffer. This expresses failed acceptance of the resolved side rather than
using the full event extreme. If an uncollected prior dealing-range external or
equilibrium lies ahead, it is recorded as a first structural protection
waypoint; the existing native strategy retains its frozen target/R logic.

This module creates only a causal schedule. NautilusTrader remains the sole
order, fill, fee, funding, margin, position and NAV engine.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from derive_nt_lvcfr_v2_signals import NS_PER_MINUTE, load_futures_minutes

INTERNAL_BOS_CONTINUATION = "INTERNAL_EVENT_RANGE_BOS_CONTINUATION"
INTERNAL_CHOCH_REVERSAL = "INTERNAL_EVENT_RANGE_CHOCH_REVERSAL"
EXTERNAL_EXPANSION_CONTINUATION = "EXTERNAL_EVENT_RANGE_EXPANSION_CONTINUATION"
EXTERNAL_FAILURE_REVERSAL = "EXTERNAL_EVENT_RANGE_FAILURE_REVERSAL"


def _rows(
    values: dict[int, dict[str, float]],
    start: int,
    end: int,
) -> list[dict[str, float]] | None:
    rows = [values.get(minute) for minute in range(start, end)]
    if any(row is None for row in rows):
        return None
    return [row for row in rows if row is not None]


def first_completed_event_range_break(
    futures: dict[int, dict[str, float]],
    *,
    start_minute: int,
    event_low: float,
    event_high: float,
    expiry_minutes: int,
) -> tuple[int, int, float, list[dict[str, float]]] | None:
    """Return (break minute, direction, close, observed rows), causally."""
    if expiry_minutes <= 0:
        raise ValueError("expiry_minutes must be positive")
    observed: list[dict[str, float]] = []
    for offset in range(expiry_minutes):
        minute = start_minute + offset
        row = futures.get(minute)
        if row is None:
            return None
        observed.append(row)
        close = float(row["close"])
        if close > event_high:
            return minute, 1, close, observed
        if close < event_low:
            return minute, -1, close, observed
    return None


def derive_v12(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
    dealing_range_minutes: int = 240,
    resolution_expiry_minutes: int = 120,
    midpoint_stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if dealing_range_minutes <= 0:
        raise ValueError("dealing_range_minutes must be positive")
    if resolution_expiry_minutes <= 0:
        raise ValueError("resolution_expiry_minutes must be positive")
    if midpoint_stop_buffer_atr < 0.0:
        raise ValueError("midpoint_stop_buffer_atr must be non-negative")

    futures = load_futures_minutes(raw_root)
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    routed: list[dict[str, Any]] = []
    state_counts: dict[str, int] = {}
    no_trade_reasons: dict[str, int] = {}
    break_wait_minutes: list[int] = []

    def count(mapping: dict[str, int], key: str) -> None:
        mapping[key] = mapping.get(key, 0) + 1

    for original in sorted(source, key=lambda item: int(item["confirm_time_ns"])):
        original_direction = int(original["direction"])
        original_confirm_ns = int(original["confirm_time_ns"])
        event_start = int(original["first_start_time_ns"]) // NS_PER_MINUTE
        event_end = original_confirm_ns // NS_PER_MINUTE
        prior = _rows(
            futures,
            event_start - dealing_range_minutes,
            event_start,
        )
        event = _rows(futures, event_start, event_end)
        if (
            prior is None
            or len(prior) != dealing_range_minutes
            or event is None
            or len(event) != 10
        ):
            count(no_trade_reasons, "MISSING_CAUSAL_CONTEXT")
            continue

        dealing_low = min(float(row["low"]) for row in prior)
        dealing_high = max(float(row["high"]) for row in prior)
        event_low = min(float(row["low"]) for row in event)
        event_high = max(float(row["high"]) for row in event)
        event_midpoint = (event_low + event_high) / 2.0
        atr = float(original["atr"])
        if not all(
            math.isfinite(value) and value > 0.0
            for value in (dealing_high - dealing_low, event_high - event_low, atr)
        ):
            count(no_trade_reasons, "INVALID_CAUSAL_CONTEXT")
            continue

        break_result = first_completed_event_range_break(
            futures,
            start_minute=event_end,
            event_low=event_low,
            event_high=event_high,
            expiry_minutes=resolution_expiry_minutes,
        )
        if break_result is None:
            count(no_trade_reasons, "EVENT_RANGE_UNRESOLVED_AT_EXPIRY")
            continue

        break_minute, direction, break_close, observed_after_event = break_result
        wait_minutes = break_minute - event_end + 1
        break_wait_minutes.append(wait_minutes)
        original_external = dealing_high if original_direction > 0 else dealing_low
        original_external_swept = (
            event_high > dealing_high
            if original_direction > 0
            else event_low < dealing_low
        )
        same_direction = direction == original_direction
        if original_external_swept:
            state = (
                EXTERNAL_EXPANSION_CONTINUATION
                if same_direction
                else EXTERNAL_FAILURE_REVERSAL
            )
        else:
            state = INTERNAL_BOS_CONTINUATION if same_direction else INTERNAL_CHOCH_REVERSAL
        entry_kind = "CONTINUATION" if same_direction else "REVERSAL"
        stop = (
            event_midpoint - midpoint_stop_buffer_atr * atr
            if direction > 0
            else event_midpoint + midpoint_stop_buffer_atr * atr
        )
        confirm_ns = (break_minute + 1) * NS_PER_MINUTE

        observed_low = min(float(row["low"]) for row in observed_after_event)
        observed_high = max(float(row["high"]) for row in observed_after_event)
        routed_external = dealing_high if direction > 0 else dealing_low
        equilibrium = (dealing_low + dealing_high) / 2.0
        structural_trigger: float | None = None
        structural_objective = "EXISTING_NET_R_OBJECTIVE"
        if direction * (routed_external - break_close) > 0.0:
            structural_trigger = routed_external
            structural_objective = "UNSWEPT_PRIOR_RANGE_EXTERNAL_WAYPOINT"
        elif not same_direction and direction * (equilibrium - break_close) > 0.0:
            structural_trigger = equilibrium
            structural_objective = "PRIOR_RANGE_EQUILIBRIUM_WAYPOINT"

        details = dict(original.get("details", {}))
        details.update(
            {
                "scenario_kind": state,
                "entry_kind": entry_kind,
                "original_direction": original_direction,
                "routed_direction": direction,
                "v1_confirm_time_ns": original_confirm_ns,
                "dealing_range_minutes": dealing_range_minutes,
                "dealing_range_low": dealing_low,
                "dealing_range_high": dealing_high,
                "dealing_range_equilibrium": equilibrium,
                "event_low": event_low,
                "event_high": event_high,
                "event_midpoint": event_midpoint,
                "original_directional_external": original_external,
                "original_directional_external_swept": original_external_swept,
                "resolution_expiry_minutes": resolution_expiry_minutes,
                "event_range_break_wait_minutes": wait_minutes,
                "event_range_break_minute": break_minute,
                "event_range_break_close": break_close,
                "observed_low_through_break": observed_low,
                "observed_high_through_break": observed_high,
                "midpoint_stop_buffer_atr": midpoint_stop_buffer_atr,
                "structural_objective": structural_objective,
            }
        )
        signal = dict(original)
        signal["scenario_id"] = (
            f"NT-LVCFR-V12-{state}-"
            + str(original["scenario_id"]).rsplit("-", 1)[-1]
        )
        signal["scenario_kind"] = state
        signal["entry_kind"] = entry_kind
        signal["direction"] = direction
        signal["confirm_time_ns"] = confirm_ns
        signal["eligible_time_ns"] = confirm_ns
        signal["initial_stop"] = stop
        signal["target_mode"] = (
            "STRUCTURAL_PROTECTION_THEN_EXISTING_OBJECTIVE"
            if structural_trigger is not None
            else "EXISTING_NET_R_OBJECTIVE"
        )
        signal["disable_rapid_failure_reversal"] = True
        signal.pop("structural_target", None)
        signal.pop("structural_protection_trigger", None)
        if structural_trigger is not None:
            signal["structural_protection_trigger"] = structural_trigger
        signal["details"] = details
        routed.append(signal)
        count(state_counts, state)

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(routed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v12-event-range-resolution",
        "engine_status": "causal_schedule_only_no_backtest",
        "source_signal_count": len(source),
        "derived_signal_count": len(routed),
        "state_counts": dict(sorted(state_counts.items())),
        "no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        "dealing_range_minutes": dealing_range_minutes,
        "resolution_expiry_minutes": resolution_expiry_minutes,
        "midpoint_stop_buffer_atr": midpoint_stop_buffer_atr,
        "minimum_break_wait_minutes": (
            min(break_wait_minutes) if break_wait_minutes else None
        ),
        "maximum_break_wait_minutes": (
            max(break_wait_minutes) if break_wait_minutes else None
        ),
        "state_sequence": [
            "LIQUIDATION_EVENT_RANGE_DEFINED",
            "WAIT_FOR_FIRST_COMPLETED_CLOSE_BEYOND_EITHER_EXTREME",
            "BOS_CONTINUATION_OR_CHOCH_REVERSAL",
            "MIDPOINT_INVALIDATION",
        ],
        "source_signals": str(source_signals),
        "output_signals": str(output_signals),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return routed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--dealing-range-minutes", type=int, default=240)
    parser.add_argument("--resolution-expiry-minutes", type=int, default=120)
    parser.add_argument("--midpoint-stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v1.json"
    if not source.exists():
        source = prepared / "signals.json"
    routed = derive_v12(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=prepared / "signals.json",
        output_manifest=args.output_manifest.resolve(),
        dealing_range_minutes=args.dealing_range_minutes,
        resolution_expiry_minutes=args.resolution_expiry_minutes,
        midpoint_stop_buffer_atr=args.midpoint_stop_buffer_atr,
    )
    print(
        json.dumps(
            {
                "signals": len(routed),
                "manifest": str(args.output_manifest.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
