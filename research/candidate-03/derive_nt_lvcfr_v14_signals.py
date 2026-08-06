#!/usr/bin/env python3
"""Combine the validated V11 router with a causal CHoCH fallback.

V11 contains the strongest validated external-liquidity, migration-reclaim and
failed-reclaim/reacceptance states, but it leaves many liquidation events
unrouted. V12 established that, among those events, a first completed close
beyond the event extreme opposite the original liquidation direction is a
useful CHoCH reversal, while a same-side first break is not a tradable BOS.

V14 therefore uses strict priority rather than blending labels:

1. Run the frozen V11/V7 scenario router unchanged.
2. Identify original V1 events for which that router emitted no signal.
3. For only those unrouted events, define the ten-minute event range and wait at
   most the already-used 120 completed minutes for the first close beyond either
   extreme.
4. Emit a fallback reversal only when the first completed break is opposite the
   original event direction. Same-side breaks and unresolved events remain
   NO_TRADE.

The fallback invalidates at the event midpoint plus/minus the frozen 0.20 ATR
buffer. The nearest prior 240-minute equilibrium or directional external ahead
is recorded as a first protection waypoint when available. Existing targets,
protection, costs, funding, 3% current-NAV sizing, orders, fills, positions and
NAV remain the NautilusTrader-native path.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from derive_nt_lvcfr_v2_signals import NS_PER_MINUTE, load_futures_minutes
from derive_nt_lvcfr_v7_signals import derive_v7

UNROUTED_EVENT_RANGE_CHOCH_FALLBACK = "UNROUTED_EVENT_RANGE_CHOCH_FALLBACK"


def _complete_rows(
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


def nearest_waypoint_ahead(
    *,
    direction: int,
    reference_price: float,
    candidates: list[tuple[str, float]],
) -> tuple[str, float] | None:
    """Return the nearest causal liquidity waypoint in the routed direction."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    ahead = [
        (name, float(value))
        for name, value in candidates
        if math.isfinite(float(value))
        and direction * (float(value) - reference_price) > 0.0
    ]
    if not ahead:
        return None
    return min(
        ahead,
        key=lambda item: direction * (item[1] - reference_price),
    )


def derive_v14(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
    dealing_range_minutes: int = 240,
    fallback_expiry_minutes: int = 120,
    fallback_stop_buffer_atr: float = 0.20,
    **v7_kwargs: Any,
) -> list[dict[str, Any]]:
    if dealing_range_minutes <= 0:
        raise ValueError("dealing_range_minutes must be positive")
    if fallback_expiry_minutes <= 0:
        raise ValueError("fallback_expiry_minutes must be positive")
    if fallback_stop_buffer_atr < 0.0:
        raise ValueError("fallback_stop_buffer_atr must be non-negative")

    original = json.loads(source_signals.read_text(encoding="utf-8"))
    v11_signals_path = output_manifest.with_name(
        output_manifest.stem + "-v11-intermediate-signals.json"
    )
    v11_manifest_path = output_manifest.with_name(
        output_manifest.stem + "-v11-intermediate.json"
    )
    v11_signals = derive_v7(
        source_signals=source_signals,
        raw_root=raw_root,
        output_signals=v11_signals_path,
        output_manifest=v11_manifest_path,
        **v7_kwargs,
    )
    routed_original_times = {
        int(
            signal.get("details", {}).get(
                "v1_confirm_time_ns",
                signal["confirm_time_ns"],
            )
        )
        for signal in v11_signals
    }

    futures = load_futures_minutes(raw_root)
    fallback_signals: list[dict[str, Any]] = []
    no_trade_reasons: dict[str, int] = {}
    wait_minutes: list[int] = []

    def count(reason: str) -> None:
        no_trade_reasons[reason] = no_trade_reasons.get(reason, 0) + 1

    for source in sorted(original, key=lambda item: int(item["confirm_time_ns"])):
        original_confirm_ns = int(source["confirm_time_ns"])
        if original_confirm_ns in routed_original_times:
            continue

        original_direction = int(source["direction"])
        event_start = int(source["first_start_time_ns"]) // NS_PER_MINUTE
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
            count("MISSING_CAUSAL_CONTEXT")
            continue

        dealing_low = min(float(row["low"]) for row in prior)
        dealing_high = max(float(row["high"]) for row in prior)
        equilibrium = (dealing_low + dealing_high) / 2.0
        event_low = min(float(row["low"]) for row in event)
        event_high = max(float(row["high"]) for row in event)
        event_midpoint = (event_low + event_high) / 2.0
        atr = float(source["atr"])
        if not all(
            math.isfinite(value) and value > 0.0
            for value in (dealing_high - dealing_low, event_high - event_low, atr)
        ):
            count("INVALID_CAUSAL_CONTEXT")
            continue

        break_result = first_completed_event_range_break(
            futures,
            start_minute=event_end,
            event_low=event_low,
            event_high=event_high,
            expiry_minutes=fallback_expiry_minutes,
        )
        if break_result is None:
            count("EVENT_RANGE_UNRESOLVED")
            continue
        break_minute, direction, break_close = break_result
        if direction == original_direction:
            count("SAME_SIDE_FIRST_BREAK_NO_FALLBACK")
            continue

        stop = event_midpoint - direction * fallback_stop_buffer_atr * atr
        if direction * (break_close - stop) <= 0.0:
            count("NON_EXECUTABLE_STRUCTURAL_STOP")
            continue

        waypoint = nearest_waypoint_ahead(
            direction=direction,
            reference_price=break_close,
            candidates=[
                ("PRIOR_RANGE_EQUILIBRIUM_WAYPOINT", equilibrium),
                (
                    "PRIOR_RANGE_EXTERNAL_WAYPOINT",
                    dealing_high if direction > 0 else dealing_low,
                ),
            ],
        )
        confirm_ns = (break_minute + 1) * NS_PER_MINUTE
        wait = break_minute - event_end + 1
        details = dict(source.get("details", {}))
        details.update(
            {
                "scenario_kind": UNROUTED_EVENT_RANGE_CHOCH_FALLBACK,
                "entry_kind": "REVERSAL",
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
                "first_break_minute": break_minute,
                "first_break_close": break_close,
                "first_break_wait_minutes": wait,
                "fallback_expiry_minutes": fallback_expiry_minutes,
                "fallback_stop_buffer_atr": fallback_stop_buffer_atr,
                "structural_waypoint_kind": (
                    None if waypoint is None else waypoint[0]
                ),
            }
        )
        suffix = str(source["scenario_id"]).rsplit("-", 1)[-1]
        signal = dict(source)
        signal["scenario_id"] = (
            f"NT-LVCFR-V14-{UNROUTED_EVENT_RANGE_CHOCH_FALLBACK}-{suffix}"
        )
        signal["scenario_kind"] = UNROUTED_EVENT_RANGE_CHOCH_FALLBACK
        signal["entry_kind"] = "REVERSAL"
        signal["direction"] = direction
        signal["confirm_time_ns"] = confirm_ns
        signal["eligible_time_ns"] = confirm_ns
        signal["initial_stop"] = stop
        signal["disable_rapid_failure_reversal"] = True
        signal.pop("structural_target", None)
        signal.pop("structural_protection_trigger", None)
        if waypoint is None:
            signal["target_mode"] = "EXISTING_NET_R_OBJECTIVE"
        else:
            signal["structural_protection_trigger"] = waypoint[1]
            signal["target_mode"] = (
                "STRUCTURAL_PROTECTION_THEN_EXISTING_OBJECTIVE"
            )
        signal["details"] = details
        fallback_signals.append(signal)
        wait_minutes.append(wait)

    combined = sorted(
        [*v11_signals, *fallback_signals],
        key=lambda item: int(item["confirm_time_ns"]),
    )
    combined_original_times = [
        int(
            signal.get("details", {}).get(
                "v1_confirm_time_ns",
                signal["confirm_time_ns"],
            )
        )
        for signal in combined
    ]
    if len(set(combined_original_times)) != len(combined_original_times):
        raise RuntimeError("multiple V14 signals route the same original event")

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(combined, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    v11_manifest = json.loads(v11_manifest_path.read_text(encoding="utf-8"))
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v14-scenario-router-choch-fallback",
        "engine_status": "causal_schedule_only_no_backtest",
        "source_signal_count": len(original),
        "v11_signal_count": len(v11_signals),
        "fallback_signal_count": len(fallback_signals),
        "derived_signal_count": len(combined),
        "v11_state_counts": v11_manifest["state_counts"],
        "fallback_state": UNROUTED_EVENT_RANGE_CHOCH_FALLBACK,
        "fallback_no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        "dealing_range_minutes": dealing_range_minutes,
        "fallback_expiry_minutes": fallback_expiry_minutes,
        "fallback_stop_buffer_atr": fallback_stop_buffer_atr,
        "minimum_fallback_wait_minutes": (
            min(wait_minutes) if wait_minutes else None
        ),
        "maximum_fallback_wait_minutes": (
            max(wait_minutes) if wait_minutes else None
        ),
        "priority_sequence": [
            "V11_VALIDATED_SCENARIO_ROUTER",
            "UNROUTED_V1_EVENT",
            "FIRST_COMPLETED_OPPOSITE_EVENT_RANGE_BREAK",
            UNROUTED_EVENT_RANGE_CHOCH_FALLBACK,
            "NO_TRADE_ON_SAME_SIDE_OR_UNRESOLVED_BREAK",
        ],
        "source_signals": str(source_signals),
        "v11_intermediate_signals": str(v11_signals_path),
        "output_signals": str(output_signals),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return combined


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--dealing-range-minutes", type=int, default=240)
    parser.add_argument("--fallback-expiry-minutes", type=int, default=120)
    parser.add_argument("--fallback-stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()

    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v1.json"
    if not source.exists():
        source = prepared / "signals.json"
    output = prepared / "signals.json"
    combined = derive_v14(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        dealing_range_minutes=args.dealing_range_minutes,
        fallback_expiry_minutes=args.fallback_expiry_minutes,
        fallback_stop_buffer_atr=args.fallback_stop_buffer_atr,
    )

    manifest = json.loads(args.output_manifest.resolve().read_text(encoding="utf-8"))
    data_manifest_path = prepared / "data_manifest.json"
    if data_manifest_path.exists():
        data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
        data_manifest["candidate"] = (
            "candidate-03-nt-lvcfr-v14-scenario-router-choch-fallback"
        )
        data_manifest["signals"] = len(combined)
        data_manifest["signal_path"] = output.as_posix()
        data_manifest["scenario_transform"] = {
            "type": "v11_priority_with_unrouted_event_range_choch_fallback",
            "priority_sequence": manifest["priority_sequence"],
            "fallback_expiry_minutes": args.fallback_expiry_minutes,
            "fallback_stop_buffer_atr": args.fallback_stop_buffer_atr,
        }
        data_manifest_path.write_text(
            json.dumps(data_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "candidate": manifest["candidate"],
                "signals": len(combined),
                "v11_signals": manifest["v11_signal_count"],
                "fallback_signals": manifest["fallback_signal_count"],
                "fallback_no_trade_reasons": manifest[
                    "fallback_no_trade_reasons"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
