#!/usr/bin/env python3
"""Derive V15 event-range auction states with cross-market flow confirmation.

V15 replaces the V11/V14 priority router after its frozen third-week failure.
Each frozen V1 OI-contraction event becomes a temporary 10-minute auction
range. The state machine waits for one of two mutually exclusive terminal
states:

1. EVENT_RANGE_CHOCH_REVERSAL: a completed minute closes beyond the event
   boundary opposite the original displacement before continuation confirms.
2. FLOW_CONFIRMED_EVENT_ACCEPTANCE: a completed minute closes beyond the
   same-side boundary, the next completed minute also holds outside, and
   cumulative futures and spot aggressive quote flow over both minutes agree
   with the original direction.

A same-side break which fails that hold/flow test is never retried. The event
may only reverse later or expire. This hysteresis prevents repeated breakout
attempts from becoming independent trades.

This module emits only causal scenario schedules. NautilusTrader remains the
sole order, fill, fee, funding, position, accounting, and NAV engine.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from nt_lvcfr_data import NS_PER_MINUTE, load_kline_minutes

EVENT_RANGE_CHOCH_REVERSAL = "EVENT_RANGE_CHOCH_REVERSAL"
FLOW_CONFIRMED_EVENT_ACCEPTANCE = "FLOW_CONFIRMED_EVENT_ACCEPTANCE"


def _ahead_waypoint(
    *,
    direction: int,
    reference_price: float,
    dealing_low: float,
    dealing_high: float,
) -> tuple[float | None, str | None]:
    equilibrium = (dealing_low + dealing_high) / 2.0
    candidates: list[tuple[float, str]] = []
    for value, name in (
        (equilibrium, "PRIOR_RANGE_EQUILIBRIUM_WAYPOINT"),
        (
            dealing_high if direction > 0 else dealing_low,
            "PRIOR_RANGE_EXTERNAL_WAYPOINT",
        ),
    ):
        if direction * (value - reference_price) > 0.0:
            candidates.append((value, name))
    if not candidates:
        return None, None
    if direction > 0:
        return min(candidates, key=lambda item: item[0])
    return max(candidates, key=lambda item: item[0])


def derive_v15(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
    dealing_range_minutes: int = 240,
    auction_expiry_minutes: int = 120,
    acceptance_hold_minutes: int = 2,
    stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if dealing_range_minutes <= 0:
        raise ValueError("dealing_range_minutes must be positive")
    if auction_expiry_minutes <= 0:
        raise ValueError("auction_expiry_minutes must be positive")
    if acceptance_hold_minutes != 2:
        raise ValueError("V15 freezes one breakout minute plus one hold minute")
    if stop_buffer_atr < 0.0:
        raise ValueError("stop_buffer_atr must be non-negative")

    futures = {
        bar.minute_index: bar
        for bar in load_kline_minutes(
            sorted((raw_root / "futures_kline").glob("*.zip"))
        )
    }
    spot = {
        bar.minute_index: bar
        for bar in load_kline_minutes(sorted((raw_root / "spot_kline").glob("*.zip")))
    }
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    routed: list[dict[str, Any]] = []
    counters: dict[str, int] = {
        "source_events": len(source),
        "event_range_choch_reversals": 0,
        "flow_confirmed_acceptances": 0,
        "failed_same_side_acceptances": 0,
        "expired_events": 0,
        "missing_data": 0,
    }
    state_counts: dict[str, int] = {}

    def count_state(name: str) -> None:
        state_counts[name] = state_counts.get(name, 0) + 1

    for source_signal in sorted(
        source,
        key=lambda item: int(item["confirm_time_ns"]),
    ):
        original_direction = int(source_signal["direction"])
        if original_direction not in {-1, 1}:
            raise ValueError("source direction must be -1 or 1")
        event_start = int(source_signal["first_start_time_ns"]) // NS_PER_MINUTE
        event_end = int(source_signal["confirm_time_ns"]) // NS_PER_MINUTE
        event_rows = [futures.get(minute) for minute in range(event_start, event_end)]
        prior_rows = [
            futures.get(minute)
            for minute in range(
                event_start - dealing_range_minutes,
                event_start,
            )
        ]
        if (
            len(event_rows) != 10
            or any(row is None for row in event_rows)
            or len(prior_rows) != dealing_range_minutes
            or any(row is None for row in prior_rows)
        ):
            counters["missing_data"] += 1
            continue
        event = [row for row in event_rows if row is not None]
        prior = [row for row in prior_rows if row is not None]
        event_low = min(row.low for row in event)
        event_high = max(row.high for row in event)
        event_midpoint = (event_low + event_high) / 2.0
        event_span = event_high - event_low
        if not math.isfinite(event_span) or event_span <= 0.0:
            counters["missing_data"] += 1
            continue
        same_side_boundary = event_high if original_direction > 0 else event_low
        opposite_boundary = event_low if original_direction > 0 else event_high
        dealing_low = min(row.low for row in prior)
        dealing_high = max(row.high for row in prior)
        atr = float(source_signal["atr"])

        same_break_minute: int | None = None
        hold_futures: list[Any] = []
        hold_spot: list[Any] = []
        same_side_attempt_failed = False
        emitted: dict[str, Any] | None = None
        observed_low = event_low
        observed_high = event_high

        for minute in range(event_end, event_end + auction_expiry_minutes):
            future_bar = futures.get(minute)
            spot_bar = spot.get(minute)
            if future_bar is None or spot_bar is None:
                counters["missing_data"] += 1
                break
            observed_low = min(observed_low, future_bar.low)
            observed_high = max(observed_high, future_bar.high)

            # Opposite-boundary failure has priority at every completed minute,
            # including while a same-side hold is pending.
            if original_direction * (future_bar.close - opposite_boundary) < 0.0:
                direction = -original_direction
                stop = (
                    observed_low - stop_buffer_atr * atr
                    if direction > 0
                    else observed_high + stop_buffer_atr * atr
                )
                waypoint, waypoint_kind = _ahead_waypoint(
                    direction=direction,
                    reference_price=future_bar.close,
                    dealing_low=dealing_low,
                    dealing_high=dealing_high,
                )
                details = dict(source_signal.get("details", {}))
                details.update(
                    {
                        "scenario_kind": EVENT_RANGE_CHOCH_REVERSAL,
                        "entry_kind": "REVERSAL",
                        "original_direction": original_direction,
                        "routed_direction": direction,
                        "event_start_minute": event_start,
                        "event_end_minute": event_end,
                        "event_low": event_low,
                        "event_high": event_high,
                        "event_midpoint": event_midpoint,
                        "event_span": event_span,
                        "same_side_boundary": same_side_boundary,
                        "opposite_boundary": opposite_boundary,
                        "opposite_break_minute": minute,
                        "opposite_break_close": future_bar.close,
                        "auction_wait_minutes": minute - event_end + 1,
                        "auction_expiry_minutes": auction_expiry_minutes,
                        "same_side_attempt_failed_first": same_side_attempt_failed,
                        "observed_low_through_confirmation": observed_low,
                        "observed_high_through_confirmation": observed_high,
                        "dealing_range_low": dealing_low,
                        "dealing_range_high": dealing_high,
                        "dealing_range_equilibrium": (
                            dealing_low + dealing_high
                        )
                        / 2.0,
                        "structural_waypoint_kind": waypoint_kind,
                    }
                )
                emitted = dict(source_signal)
                emitted.update(
                    {
                        "scenario_id": str(source_signal["scenario_id"]).replace(
                            "NT-LVCFR-",
                            "NT-LVCFR-V15-EVENT_RANGE_CHOCH_REVERSAL-",
                            1,
                        ),
                        "scenario_kind": EVENT_RANGE_CHOCH_REVERSAL,
                        "entry_kind": "REVERSAL",
                        "direction": direction,
                        "confirm_time_ns": (minute + 1) * NS_PER_MINUTE,
                        "eligible_time_ns": (minute + 1) * NS_PER_MINUTE,
                        "initial_stop": stop,
                        "target_mode": (
                            "STRUCTURAL_PROTECTION_THEN_EXISTING_OBJECTIVE"
                        ),
                        "disable_rapid_failure_reversal": True,
                        "details": details,
                    }
                )
                emitted.pop("structural_target", None)
                if waypoint is None:
                    emitted.pop("structural_protection_trigger", None)
                else:
                    emitted["structural_protection_trigger"] = waypoint
                counters["event_range_choch_reversals"] += 1
                count_state(EVENT_RANGE_CHOCH_REVERSAL)
                break

            if same_break_minute is None and not same_side_attempt_failed:
                if original_direction * (
                    future_bar.close - same_side_boundary
                ) > 0.0:
                    same_break_minute = minute
                    hold_futures = [future_bar]
                    hold_spot = [spot_bar]
                    continue
            elif same_break_minute is not None:
                hold_futures.append(future_bar)
                hold_spot.append(spot_bar)
                if len(hold_futures) < acceptance_hold_minutes:
                    continue

                directional_futures_flow = original_direction * (
                    sum(row.signed_notional for row in hold_futures)
                    / sum(row.notional for row in hold_futures)
                )
                directional_spot_flow = original_direction * (
                    sum(row.signed_notional for row in hold_spot)
                    / sum(row.notional for row in hold_spot)
                )
                held_outside = all(
                    original_direction * (row.close - same_side_boundary) > 0.0
                    for row in hold_futures
                )
                if (
                    held_outside
                    and directional_futures_flow > 0.0
                    and directional_spot_flow > 0.0
                ):
                    direction = original_direction
                    stop = event_midpoint - direction * stop_buffer_atr * atr
                    details = dict(source_signal.get("details", {}))
                    details.update(
                        {
                            "scenario_kind": FLOW_CONFIRMED_EVENT_ACCEPTANCE,
                            "entry_kind": "CONTINUATION",
                            "original_direction": original_direction,
                            "routed_direction": direction,
                            "event_start_minute": event_start,
                            "event_end_minute": event_end,
                            "event_low": event_low,
                            "event_high": event_high,
                            "event_midpoint": event_midpoint,
                            "event_span": event_span,
                            "same_side_boundary": same_side_boundary,
                            "opposite_boundary": opposite_boundary,
                            "same_side_break_minute": same_break_minute,
                            "acceptance_confirm_minute": minute,
                            "acceptance_hold_minutes": acceptance_hold_minutes,
                            "directional_futures_flow": directional_futures_flow,
                            "directional_spot_flow": directional_spot_flow,
                            "held_outside_event_range": held_outside,
                            "auction_wait_minutes": minute - event_end + 1,
                            "auction_expiry_minutes": auction_expiry_minutes,
                            "dealing_range_low": dealing_low,
                            "dealing_range_high": dealing_high,
                            "dealing_range_equilibrium": (
                                dealing_low + dealing_high
                            )
                            / 2.0,
                            "structural_waypoint_kind": (
                                "EVENT_RANGE_BREAK_BOUNDARY"
                            ),
                        }
                    )
                    emitted = dict(source_signal)
                    emitted.update(
                        {
                            "scenario_id": str(source_signal["scenario_id"]).replace(
                                "NT-LVCFR-",
                                "NT-LVCFR-V15-FLOW_CONFIRMED_EVENT_ACCEPTANCE-",
                                1,
                            ),
                            "scenario_kind": FLOW_CONFIRMED_EVENT_ACCEPTANCE,
                            "entry_kind": "CONTINUATION",
                            "direction": direction,
                            "confirm_time_ns": (minute + 1) * NS_PER_MINUTE,
                            "eligible_time_ns": (minute + 1) * NS_PER_MINUTE,
                            "initial_stop": stop,
                            "structural_protection_trigger": same_side_boundary,
                            "target_mode": (
                                "STRUCTURAL_PROTECTION_THEN_EXISTING_OBJECTIVE"
                            ),
                            "details": details,
                        }
                    )
                    emitted.pop("structural_target", None)
                    counters["flow_confirmed_acceptances"] += 1
                    count_state(FLOW_CONFIRMED_EVENT_ACCEPTANCE)
                    break

                same_side_attempt_failed = True
                same_break_minute = None
                hold_futures = []
                hold_spot = []
                counters["failed_same_side_acceptances"] += 1

        if emitted is None:
            counters["expired_events"] += 1
            continue
        routed.append(emitted)

    routed.sort(key=lambda item: int(item["confirm_time_ns"]))
    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(routed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v15-flow-impact-auction",
        "engine_status": "causal_schedule_only_no_backtest",
        "source_signal_count": len(source),
        "derived_signal_count": len(routed),
        "state_counts": state_counts,
        "counters": counters,
        "dealing_range_minutes": dealing_range_minutes,
        "auction_expiry_minutes": auction_expiry_minutes,
        "acceptance_hold_minutes": acceptance_hold_minutes,
        "stop_buffer_atr": stop_buffer_atr,
        "priority_sequence": [
            "FROZEN_V1_OI_CONTRACTION_EVENT",
            "EVENT_RANGE_AUCTION",
            "OPPOSITE_COMPLETED_BREAK_CHOCH_REVERSAL",
            "SAME_SIDE_COMPLETED_BREAK_PENDING",
            "SECOND_COMPLETED_MINUTE_HOLD_AND_CROSS_MARKET_FLOW_AGREEMENT",
            "FLOW_CONFIRMED_EVENT_ACCEPTANCE",
            "FAILED_SAME_SIDE_ATTEMPT_REVERSAL_ONLY",
            "NO_TRADE_ON_EXPIRY",
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
    parser.add_argument("--auction-expiry-minutes", type=int, default=120)
    parser.add_argument("--acceptance-hold-minutes", type=int, default=2)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()

    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v1.json"
    if not source.exists():
        source = prepared / "signals.json"
    routed = derive_v15(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=prepared / "signals.json",
        output_manifest=args.output_manifest.resolve(),
        dealing_range_minutes=args.dealing_range_minutes,
        auction_expiry_minutes=args.auction_expiry_minutes,
        acceptance_hold_minutes=args.acceptance_hold_minutes,
        stop_buffer_atr=args.stop_buffer_atr,
    )
    print(
        json.dumps(
            {
                "derived_signals": len(routed),
                "signals_path": str(prepared / "signals.json"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
