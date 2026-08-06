#!/usr/bin/env python3
"""Derive V13 sequential event-auction states from frozen V1 events.

V12 proved that a first completed break opposite the liquidation event direction
is a useful CHoCH reversal, while a first same-side break is not sufficient BOS
acceptance. V13 therefore treats the same-side break as a pending auction rather
than an entry.

State sequence
--------------
1. A ten-minute liquidation/OI-contraction event defines a causal range.
2. Wait up to the already-used 120 completed minutes for the first close beyond
   either event extreme.
3. An opposite-side first break confirms an immediate CHoCH reversal.
4. A same-side first break opens a pending auction. Two structural outcomes then
   compete for at most 120 completed minutes:
   - one full event-range measured extension beyond the broken extreme confirms
     acceptance and continuation;
   - a completed close through the event midpoint confirms failed acceptance
     and CHoCH reversal.
5. The first completed outcome wins. An unresolved auction emits no trade.

The event range and midpoint are endogenous market structure, not fitted return
thresholds. Stops use the frozen 0.20 ATR buffer. Targets, protection, fees,
funding, 3% current-NAV risk sizing, orders, fills, positions and NAV remain the
existing NautilusTrader-native implementation.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from derive_nt_lvcfr_v2_signals import NS_PER_MINUTE, load_futures_minutes

FIRST_BREAK_CHOCH_REVERSAL = "FIRST_BREAK_CHOCH_REVERSAL"
MEASURED_ACCEPTANCE_CONTINUATION = "MEASURED_ACCEPTANCE_CONTINUATION"
MIDPOINT_FAILURE_CHOCH_REVERSAL = "MIDPOINT_FAILURE_CHOCH_REVERSAL"


def _complete_rows(
    values: dict[int, dict[str, float]],
    start: int,
    end: int,
) -> list[dict[str, float]] | None:
    rows = [values.get(minute) for minute in range(start, end)]
    if any(row is None for row in rows):
        return None
    return [row for row in rows if row is not None]


def first_completed_range_break(
    futures: dict[int, dict[str, float]],
    *,
    start_minute: int,
    event_low: float,
    event_high: float,
    expiry_minutes: int,
) -> tuple[int, int, float] | None:
    """Return the first completed close beyond either event extreme."""
    if expiry_minutes <= 0:
        raise ValueError("expiry_minutes must be positive")
    for offset in range(expiry_minutes):
        minute = start_minute + offset
        row = futures.get(minute)
        if row is None:
            return None
        close = float(row["close"])
        if close > event_high:
            return minute, 1, close
        if close < event_low:
            return minute, -1, close
    return None


def resolve_same_side_pending_auction(
    futures: dict[int, dict[str, float]],
    *,
    start_minute: int,
    first_break_direction: int,
    event_low: float,
    event_high: float,
    expiry_minutes: int,
) -> tuple[int, str, int, float] | None:
    """Resolve measured acceptance versus midpoint failure causally.

    The first completed close to reach either structural boundary wins. Missing
    minutes invalidate the pending sequence instead of silently skipping time.
    """
    if first_break_direction not in (-1, 1):
        raise ValueError("first_break_direction must be -1 or 1")
    if expiry_minutes <= 0:
        raise ValueError("expiry_minutes must be positive")
    event_span = event_high - event_low
    if not math.isfinite(event_span) or event_span <= 0.0:
        raise ValueError("event range must be positive")
    midpoint = (event_low + event_high) / 2.0
    measured_extension = (
        event_high + event_span
        if first_break_direction > 0
        else event_low - event_span
    )
    for offset in range(expiry_minutes):
        minute = start_minute + offset
        row = futures.get(minute)
        if row is None:
            return None
        close = float(row["close"])
        if first_break_direction * (close - measured_extension) >= 0.0:
            return (
                minute,
                MEASURED_ACCEPTANCE_CONTINUATION,
                first_break_direction,
                close,
            )
        if first_break_direction * (close - midpoint) < 0.0:
            return (
                minute,
                MIDPOINT_FAILURE_CHOCH_REVERSAL,
                -first_break_direction,
                close,
            )
    return None


def _first_waypoint_ahead(
    *,
    direction: int,
    entry_reference: float,
    candidates: list[tuple[str, float]],
) -> tuple[str, float] | None:
    ahead = [
        (name, value)
        for name, value in candidates
        if math.isfinite(value) and direction * (value - entry_reference) > 0.0
    ]
    if not ahead:
        return None
    return min(ahead, key=lambda item: direction * (item[1] - entry_reference))


def derive_v13(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
    dealing_range_minutes: int = 240,
    first_break_expiry_minutes: int = 120,
    pending_auction_expiry_minutes: int = 120,
    stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if dealing_range_minutes <= 0:
        raise ValueError("dealing_range_minutes must be positive")
    if stop_buffer_atr < 0.0:
        raise ValueError("stop_buffer_atr must be non-negative")

    futures = load_futures_minutes(raw_root)
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    routed: list[dict[str, Any]] = []
    state_counts: dict[str, int] = {}
    no_trade_reasons: dict[str, int] = {}
    wait_minutes: list[int] = []

    def count(mapping: dict[str, int], key: str) -> None:
        mapping[key] = mapping.get(key, 0) + 1

    for original in sorted(source, key=lambda item: int(item["confirm_time_ns"])):
        original_direction = int(original["direction"])
        original_confirm_ns = int(original["confirm_time_ns"])
        event_start = int(original["first_start_time_ns"]) // NS_PER_MINUTE
        event_end = original_confirm_ns // NS_PER_MINUTE
        prior = _complete_rows(
            futures,
            event_start - dealing_range_minutes,
            event_start,
        )
        event = _complete_rows(futures, event_start, event_end)
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
        equilibrium = (dealing_low + dealing_high) / 2.0
        event_low = min(float(row["low"]) for row in event)
        event_high = max(float(row["high"]) for row in event)
        event_midpoint = (event_low + event_high) / 2.0
        event_span = event_high - event_low
        atr = float(original["atr"])
        if not all(
            math.isfinite(value) and value > 0.0
            for value in (dealing_high - dealing_low, event_span, atr)
        ):
            count(no_trade_reasons, "INVALID_CAUSAL_CONTEXT")
            continue

        first_break = first_completed_range_break(
            futures,
            start_minute=event_end,
            event_low=event_low,
            event_high=event_high,
            expiry_minutes=first_break_expiry_minutes,
        )
        if first_break is None:
            count(no_trade_reasons, "EVENT_RANGE_UNRESOLVED")
            continue
        first_minute, first_direction, first_close = first_break
        first_wait = first_minute - event_end + 1

        if first_direction != original_direction:
            signal_minute = first_minute
            state = FIRST_BREAK_CHOCH_REVERSAL
            direction = first_direction
            entry_kind = "REVERSAL"
            signal_close = first_close
            stop_anchor = event_midpoint
            resolution_wait = 0
        else:
            pending = resolve_same_side_pending_auction(
                futures,
                start_minute=first_minute + 1,
                first_break_direction=first_direction,
                event_low=event_low,
                event_high=event_high,
                expiry_minutes=pending_auction_expiry_minutes,
            )
            if pending is None:
                count(no_trade_reasons, "SAME_SIDE_BREAK_PENDING_AUCTION_UNRESOLVED")
                continue
            signal_minute, state, direction, signal_close = pending
            entry_kind = (
                "CONTINUATION"
                if state == MEASURED_ACCEPTANCE_CONTINUATION
                else "REVERSAL"
            )
            stop_anchor = event_high if first_direction > 0 else event_low
            resolution_wait = signal_minute - first_minute

        stop = stop_anchor - direction * stop_buffer_atr * atr
        if direction * (signal_close - stop) <= 0.0:
            count(no_trade_reasons, "NON_EXECUTABLE_STRUCTURAL_STOP")
            continue

        if state == MIDPOINT_FAILURE_CHOCH_REVERSAL:
            opposite_event_extreme = event_high if direction > 0 else event_low
            waypoint_candidates = [
                ("OPPOSITE_EVENT_EXTREME_WAYPOINT", opposite_event_extreme),
                (
                    "PRIOR_RANGE_EXTERNAL_WAYPOINT",
                    dealing_high if direction > 0 else dealing_low,
                ),
            ]
        else:
            waypoint_candidates = [
                (
                    "PRIOR_RANGE_EXTERNAL_WAYPOINT",
                    dealing_high if direction > 0 else dealing_low,
                ),
                ("PRIOR_RANGE_EQUILIBRIUM_WAYPOINT", equilibrium),
            ]
        waypoint = _first_waypoint_ahead(
            direction=direction,
            entry_reference=signal_close,
            candidates=waypoint_candidates,
        )

        confirm_ns = (signal_minute + 1) * NS_PER_MINUTE
        details = dict(original.get("details", {}))
        details.update(
            {
                "scenario_kind": state,
                "entry_kind": entry_kind,
                "original_direction": original_direction,
                "first_break_direction": first_direction,
                "routed_direction": direction,
                "v1_confirm_time_ns": original_confirm_ns,
                "dealing_range_minutes": dealing_range_minutes,
                "dealing_range_low": dealing_low,
                "dealing_range_high": dealing_high,
                "dealing_range_equilibrium": equilibrium,
                "event_low": event_low,
                "event_high": event_high,
                "event_midpoint": event_midpoint,
                "event_span": event_span,
                "first_break_expiry_minutes": first_break_expiry_minutes,
                "first_break_wait_minutes": first_wait,
                "first_break_close": first_close,
                "pending_auction_expiry_minutes": pending_auction_expiry_minutes,
                "pending_resolution_wait_minutes": resolution_wait,
                "resolution_close": signal_close,
                "stop_anchor": stop_anchor,
                "stop_buffer_atr": stop_buffer_atr,
                "structural_waypoint_kind": None if waypoint is None else waypoint[0],
            }
        )
        signal = dict(original)
        signal["scenario_id"] = (
            f"NT-LVCFR-V13-{state}-"
            + str(original["scenario_id"]).rsplit("-", 1)[-1]
        )
        signal["scenario_kind"] = state
        signal["entry_kind"] = entry_kind
        signal["direction"] = direction
        signal["confirm_time_ns"] = confirm_ns
        signal["eligible_time_ns"] = confirm_ns
        signal["initial_stop"] = stop
        signal["disable_rapid_failure_reversal"] = True
        signal.pop("structural_target", None)
        signal.pop("structural_protection_trigger", None)
        if waypoint is not None:
            signal["structural_protection_trigger"] = waypoint[1]
            signal["target_mode"] = "STRUCTURAL_PROTECTION_THEN_EXISTING_OBJECTIVE"
        else:
            signal["target_mode"] = "EXISTING_NET_R_OBJECTIVE"
        signal["details"] = details
        routed.append(signal)
        count(state_counts, state)
        wait_minutes.append(first_wait + resolution_wait)

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(routed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v13-sequential-event-auction",
        "engine_status": "causal_schedule_only_no_backtest",
        "source_signal_count": len(source),
        "derived_signal_count": len(routed),
        "state_counts": dict(sorted(state_counts.items())),
        "no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        "dealing_range_minutes": dealing_range_minutes,
        "first_break_expiry_minutes": first_break_expiry_minutes,
        "pending_auction_expiry_minutes": pending_auction_expiry_minutes,
        "stop_buffer_atr": stop_buffer_atr,
        "minimum_total_wait_minutes": min(wait_minutes) if wait_minutes else None,
        "maximum_total_wait_minutes": max(wait_minutes) if wait_minutes else None,
        "state_sequence": [
            "LIQUIDATION_EVENT_RANGE_DEFINED",
            "FIRST_COMPLETED_RANGE_BREAK",
            "OPPOSITE_BREAK_IMMEDIATE_CHOCH_OR_SAME_SIDE_PENDING_AUCTION",
            "MEASURED_ACCEPTANCE_VERSUS_MIDPOINT_FAILURE",
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
    parser.add_argument("--first-break-expiry-minutes", type=int, default=120)
    parser.add_argument("--pending-auction-expiry-minutes", type=int, default=120)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v1.json"
    if not source.exists():
        source = prepared / "signals.json"
    routed = derive_v13(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=prepared / "signals.json",
        output_manifest=args.output_manifest.resolve(),
        dealing_range_minutes=args.dealing_range_minutes,
        first_break_expiry_minutes=args.first_break_expiry_minutes,
        pending_auction_expiry_minutes=args.pending_auction_expiry_minutes,
        stop_buffer_atr=args.stop_buffer_atr,
    )
    print(
        json.dumps(
            {"signals": len(routed), "manifest": str(args.output_manifest.resolve())},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
