#!/usr/bin/env python3
"""Derive failed-FVG-auction retest reversals from frozen V19 states.

V20 showed that entering in the source direction after a completed FVG midpoint
"defense" was not portable: the three fixed BTC weeks produced no winning
native episodes. V21 does not tune that entry. It trades the opposite auction
only after the original directional thesis has objectively failed:

1. Start from a frozen V19 continuation state.
2. Locate a same-direction displacement FVG already known to the strategy.
3. Require a completed close through the FVG far edge. This is a failed auction,
   whether the gap had appeared defended earlier or failed immediately.
4. Do not chase the failure impulse. Wait for price to retest the failed gap from
   the opposite side.
5. Require a completed rejection candle to close back outside the far edge in
   the reversal direction; enter only on the next native quote.
6. Invalidate beyond the failed gap/retest extreme plus the frozen 0.20 ATR
   buffer. Target the opposite external liquidity of the pre-event 240-minute
   dealing range, using event structure/equilibrium only as causal protection
   waypoints.

This module creates only a causal signal schedule. NautilusTrader remains the
sole order, fill, fee, funding, margin, position, PnL and NAV engine.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from derive_nt_lvcfr_v2_signals import NS_PER_MINUTE, load_futures_minutes
from derive_nt_lvcfr_v20_signals import FairValueGap, find_active_displacement_fvg

SOURCE_STATES = frozenset(
    {
        "MEASURED_ACCEPTANCE_CONTINUATION",
        "EXECUTED_FLOW_VACUUM_CONTINUATION",
    }
)
OUTPUT_STATE = "FAILED_FVG_AUCTION_RETEST_REVERSAL"


@dataclass(frozen=True, slots=True)
class FvgFailure:
    failure_minute: int
    failure_mode: str
    defended_minute: int | None
    touches_before_failure: int


def _complete_rows(
    values: dict[int, dict[str, float]],
    start: int,
    end: int,
) -> list[dict[str, float]] | None:
    rows = [values.get(minute) for minute in range(start, end)]
    if any(row is None for row in rows):
        return None
    return [row for row in rows if row is not None]


def find_fvg_failure(
    futures: dict[int, dict[str, float]],
    *,
    gap: FairValueGap,
    start_minute: int,
    expiry_minutes: int,
) -> tuple[FvgFailure | None, str]:
    """Return the first completed far-edge failure, preserving prior defense.

    The function accumulates only completed bars in chronological order. A
    missing minute invalidates the sequence rather than silently compressing
    time.
    """
    if expiry_minutes <= 0:
        raise ValueError("expiry_minutes must be positive")
    defended_minute: int | None = None
    touches = 0
    for offset in range(expiry_minutes):
        minute = start_minute + offset
        row = futures.get(minute)
        if row is None:
            return None, "MISSING_FAILURE_MINUTE"
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        failed = close <= gap.lower if gap.direction > 0 else close >= gap.upper
        if failed:
            mode = (
                "DEFENDED_FVG_FAILURE"
                if defended_minute is not None
                else "UNDEFENDED_FVG_FAILURE"
            )
            return (
                FvgFailure(
                    failure_minute=minute,
                    failure_mode=mode,
                    defended_minute=defended_minute,
                    touches_before_failure=touches,
                ),
                mode,
            )

        touched = low <= gap.upper and high >= gap.lower
        if not touched:
            continue
        touches += 1
        defended = (
            close > gap.midpoint and close > open_price
            if gap.direction > 0
            else close < gap.midpoint and close < open_price
        )
        if defended and defended_minute is None:
            defended_minute = minute
    return None, "FVG_FAILURE_UNRESOLVED"


def find_failed_gap_retest_rejection(
    futures: dict[int, dict[str, float]],
    *,
    gap: FairValueGap,
    start_minute: int,
    expiry_minutes: int,
) -> tuple[int, dict[str, float], int] | tuple[None, None, str]:
    """Wait for a retest of the failed gap and a completed opposite rejection.

    For a failed bullish FVG, price must retest from below and close below the
    lower edge with a bearish body. For a failed bearish FVG the mirror image is
    required. A completed reclaim through the opposite edge invalidates the
    reversal before entry.
    """
    if expiry_minutes <= 0:
        raise ValueError("expiry_minutes must be positive")
    touches = 0
    for offset in range(expiry_minutes):
        minute = start_minute + offset
        row = futures.get(minute)
        if row is None:
            return None, None, "MISSING_FAILED_GAP_RETEST_MINUTE"
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        if gap.direction > 0 and close >= gap.upper:
            return None, None, "FAILED_GAP_FULLY_RECLAIMED"
        if gap.direction < 0 and close <= gap.lower:
            return None, None, "FAILED_GAP_FULLY_RECLAIMED"

        touched = low <= gap.upper and high >= gap.lower
        if not touched:
            continue
        touches += 1
        rejected = (
            close < gap.lower and close < open_price
            if gap.direction > 0
            else close > gap.upper and close > open_price
        )
        if rejected:
            return minute, row, touches
    return None, None, "FAILED_GAP_RETEST_UNRESOLVED"


def _nearest_structural_waypoint(
    *,
    direction: int,
    entry_reference: float,
    final_target: float,
    candidates: list[float],
) -> float | None:
    ahead = [
        value
        for value in candidates
        if math.isfinite(value)
        and direction * (value - entry_reference) > 0.0
        and direction * (final_target - value) >= 0.0
    ]
    if not ahead:
        return None
    return min(ahead, key=lambda value: direction * (value - entry_reference))


def derive_v21(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
    dealing_range_minutes: int = 240,
    structural_leg_lookback_minutes: int = 120,
    formation_expiry_minutes: int = 3,
    failure_expiry_minutes: int = 120,
    retest_expiry_minutes: int = 120,
    stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if dealing_range_minutes <= 0 or structural_leg_lookback_minutes <= 0:
        raise ValueError("lookbacks must be positive")
    if formation_expiry_minutes < 0:
        raise ValueError("formation_expiry_minutes must be non-negative")
    if failure_expiry_minutes <= 0 or retest_expiry_minutes <= 0:
        raise ValueError("failure/retest expiry must be positive")
    if stop_buffer_atr < 0.0:
        raise ValueError("stop_buffer_atr must be non-negative")

    futures = load_futures_minutes(raw_root)
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    derived_by_key: dict[tuple[int, int], dict[str, Any]] = {}
    source_state_counts: dict[str, int] = {}
    failure_mode_counts: dict[str, int] = {}
    formation_modes: dict[str, int] = {}
    no_trade_reasons: dict[str, int] = {}
    failure_waits: list[int] = []
    retest_waits: list[int] = []
    gross_rrs: list[float] = []
    deduplicated = 0

    def count(mapping: dict[str, int], key: str) -> None:
        mapping[key] = mapping.get(key, 0) + 1

    for original in sorted(source, key=lambda item: int(item["confirm_time_ns"])):
        source_state = str(original.get("scenario_kind", ""))
        count(source_state_counts, source_state or "UNKNOWN")
        if source_state not in SOURCE_STATES:
            count(no_trade_reasons, "SOURCE_STATE_NOT_ROUTED")
            continue
        source_direction = int(original["direction"])
        if source_direction not in (-1, 1):
            count(no_trade_reasons, "INVALID_DIRECTION")
            continue
        atr = float(original["atr"])
        if not math.isfinite(atr) or atr <= 0.0:
            count(no_trade_reasons, "INVALID_ATR")
            continue

        source_confirm_ns = int(original["confirm_time_ns"])
        known_minute = source_confirm_ns // NS_PER_MINUTE - 1
        event_start_minute = int(original["first_start_time_ns"]) // NS_PER_MINUTE
        event_end_minute = int(original["first_end_time_ns"]) // NS_PER_MINUTE
        structural_start = max(
            event_end_minute,
            known_minute - structural_leg_lookback_minutes,
        )
        gap, formation_mode = find_active_displacement_fvg(
            futures,
            direction=source_direction,
            structural_start_minute=structural_start,
            known_minute=known_minute,
            formation_expiry_minutes=formation_expiry_minutes,
        )
        if gap is None:
            count(no_trade_reasons, formation_mode)
            continue
        count(formation_modes, formation_mode)

        failure_start = max(known_minute + 1, gap.formed_minute + 1)
        failure, failure_reason = find_fvg_failure(
            futures,
            gap=gap,
            start_minute=failure_start,
            expiry_minutes=failure_expiry_minutes,
        )
        if failure is None:
            count(no_trade_reasons, failure_reason)
            continue
        count(failure_mode_counts, failure.failure_mode)

        retest = find_failed_gap_retest_rejection(
            futures,
            gap=gap,
            start_minute=failure.failure_minute + 1,
            expiry_minutes=retest_expiry_minutes,
        )
        if retest[0] is None:
            count(no_trade_reasons, str(retest[2]))
            continue
        rejection_minute = int(retest[0])
        rejection_row = retest[1]
        retest_touches = int(retest[2])
        assert isinstance(rejection_row, dict)

        prior = _complete_rows(
            futures,
            event_start_minute - dealing_range_minutes,
            event_start_minute,
        )
        event = _complete_rows(
            futures,
            event_start_minute,
            max(event_start_minute + 1, event_end_minute),
        )
        if prior is None or len(prior) != dealing_range_minutes:
            count(no_trade_reasons, "MISSING_PRE_EVENT_DEALING_RANGE")
            continue
        if event is None or not event:
            count(no_trade_reasons, "MISSING_EVENT_RANGE")
            continue

        dealing_low = min(float(row["low"]) for row in prior)
        dealing_high = max(float(row["high"]) for row in prior)
        dealing_equilibrium = (dealing_low + dealing_high) / 2.0
        original_details = dict(original.get("details", {}))
        event_low = float(
            original_details.get(
                "event_low", min(float(row["low"]) for row in event)
            )
        )
        event_high = float(
            original_details.get(
                "event_high", max(float(row["high"]) for row in event)
            )
        )
        event_midpoint = float(
            original_details.get(
                "event_midpoint", (event_low + event_high) / 2.0
            )
        )

        direction = -source_direction
        entry_reference = float(rejection_row["close"])
        final_target = dealing_high if direction > 0 else dealing_low
        if (
            not math.isfinite(final_target)
            or direction * (final_target - entry_reference) <= 0.0
        ):
            count(no_trade_reasons, "NO_OPPOSITE_EXTERNAL_LIQUIDITY_AHEAD")
            continue

        if direction > 0:
            stop_anchor = min(gap.lower, float(rejection_row["low"]))
            waypoint_candidates = [event_midpoint, event_high, dealing_equilibrium]
        else:
            stop_anchor = max(gap.upper, float(rejection_row["high"]))
            waypoint_candidates = [event_midpoint, event_low, dealing_equilibrium]
        stop = stop_anchor - direction * stop_buffer_atr * atr
        risk_distance = direction * (entry_reference - stop)
        reward_distance = direction * (final_target - entry_reference)
        if not math.isfinite(stop) or risk_distance <= 0.0:
            count(no_trade_reasons, "NON_EXECUTABLE_FAILED_GAP_INVALIDATION")
            continue
        gross_rr = reward_distance / risk_distance
        waypoint = _nearest_structural_waypoint(
            direction=direction,
            entry_reference=entry_reference,
            final_target=final_target,
            candidates=waypoint_candidates,
        )

        confirm_ns = (rejection_minute + 1) * NS_PER_MINUTE
        details = original_details
        details.update(
            {
                "scenario_kind": OUTPUT_STATE,
                "entry_kind": "REVERSAL",
                "source_v19_scenario_id": original["scenario_id"],
                "source_v19_state": source_state,
                "source_v19_direction": source_direction,
                "source_v19_confirm_time_ns": source_confirm_ns,
                "routed_direction": direction,
                "entry_sequence": [
                    "V19_DIRECTIONAL_CONTINUATION_STATE",
                    "DIRECTIONAL_DISPLACEMENT_FVG",
                    "COMPLETED_FAR_EDGE_FAILURE",
                    "FAILED_GAP_RETEST_FROM_OPPOSITE_SIDE",
                    "COMPLETED_REJECTION_OUTSIDE_FAR_EDGE",
                    "NEXT_NATIVE_QUOTE_ENTRY",
                ],
                "formation_mode": formation_mode,
                "fvg_formed_minute": gap.formed_minute,
                "fvg_lower": gap.lower,
                "fvg_upper": gap.upper,
                "fvg_midpoint": gap.midpoint,
                "failure_mode": failure.failure_mode,
                "failure_minute": failure.failure_minute,
                "prior_defense_minute": failure.defended_minute,
                "touches_before_failure": failure.touches_before_failure,
                "failure_wait_minutes": failure.failure_minute - gap.formed_minute,
                "retest_wait_minutes": rejection_minute - failure.failure_minute,
                "retest_touch_count": retest_touches,
                "rejection_minute": rejection_minute,
                "rejection_open": float(rejection_row["open"]),
                "rejection_high": float(rejection_row["high"]),
                "rejection_low": float(rejection_row["low"]),
                "rejection_close": entry_reference,
                "stop_anchor": stop_anchor,
                "stop_buffer_atr": stop_buffer_atr,
                "dealing_range_minutes": dealing_range_minutes,
                "dealing_range_low": dealing_low,
                "dealing_range_high": dealing_high,
                "dealing_range_equilibrium": dealing_equilibrium,
                "event_low": event_low,
                "event_high": event_high,
                "event_midpoint": event_midpoint,
                "structural_target": final_target,
                "structural_waypoint": waypoint,
                "gross_structural_rr_at_rejection_close": gross_rr,
                "failure_expiry_minutes": failure_expiry_minutes,
                "retest_expiry_minutes": retest_expiry_minutes,
            }
        )
        signal = dict(original)
        suffix = str(original["scenario_id"]).rsplit("-", 1)[-1]
        signal["scenario_id"] = f"NT-LVCFR-V21-{OUTPUT_STATE}-{suffix}"
        signal["scenario_kind"] = OUTPUT_STATE
        signal["entry_kind"] = "REVERSAL"
        signal["direction"] = direction
        signal["confirm_time_ns"] = confirm_ns
        signal["eligible_time_ns"] = confirm_ns
        signal["initial_stop"] = stop
        signal["structural_target"] = final_target
        signal["target_mode"] = "STRUCTURAL_LIQUIDITY_OBJECTIVE"
        signal["disable_rapid_failure_reversal"] = True
        if waypoint is not None:
            signal["structural_protection_trigger"] = waypoint
        else:
            signal.pop("structural_protection_trigger", None)
        signal["details"] = details

        key = (confirm_ns, direction)
        if key in derived_by_key:
            deduplicated += 1
            continue
        derived_by_key[key] = signal
        failure_waits.append(failure.failure_minute - gap.formed_minute)
        retest_waits.append(rejection_minute - failure.failure_minute)
        gross_rrs.append(gross_rr)

    derived = sorted(
        derived_by_key.values(),
        key=lambda item: (int(item["confirm_time_ns"]), str(item["scenario_id"])),
    )
    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(derived, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ordered_rrs = sorted(gross_rrs)
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v21-failed-fvg-auction-retest",
        "engine_status": "causal_schedule_only_no_backtest",
        "source_signal_count": len(source),
        "derived_signal_count": len(derived),
        "source_states_routed": sorted(SOURCE_STATES),
        "source_state_counts": dict(sorted(source_state_counts.items())),
        "output_state_counts": ({OUTPUT_STATE: len(derived)} if derived else {}),
        "formation_modes": dict(sorted(formation_modes.items())),
        "failure_mode_counts": dict(sorted(failure_mode_counts.items())),
        "no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        "deduplicated_same_retest_opportunities": deduplicated,
        "dealing_range_minutes": dealing_range_minutes,
        "structural_leg_lookback_minutes": structural_leg_lookback_minutes,
        "formation_expiry_minutes": formation_expiry_minutes,
        "failure_expiry_minutes": failure_expiry_minutes,
        "retest_expiry_minutes": retest_expiry_minutes,
        "stop_buffer_atr": stop_buffer_atr,
        "minimum_failure_wait_minutes": min(failure_waits) if failure_waits else None,
        "maximum_failure_wait_minutes": max(failure_waits) if failure_waits else None,
        "minimum_retest_wait_minutes": min(retest_waits) if retest_waits else None,
        "maximum_retest_wait_minutes": max(retest_waits) if retest_waits else None,
        "minimum_gross_structural_rr": min(gross_rrs) if gross_rrs else None,
        "median_gross_structural_rr": (
            ordered_rrs[len(ordered_rrs) // 2] if ordered_rrs else None
        ),
        "selection_policy": (
            "fixed failed-auction sequence; no return-fit threshold search; "
            "NautilusTrader execution/risk/cost configuration unchanged"
        ),
        "source_signals": str(source_signals),
        "output_signals": str(output_signals),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return derived


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--source-signals", type=Path)
    parser.add_argument("--output-signals", type=Path)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--dealing-range-minutes", type=int, default=240)
    parser.add_argument("--structural-leg-lookback-minutes", type=int, default=120)
    parser.add_argument("--formation-expiry-minutes", type=int, default=3)
    parser.add_argument("--failure-expiry-minutes", type=int, default=120)
    parser.add_argument("--retest-expiry-minutes", type=int, default=120)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    source = (args.source_signals or (prepared / "signals-v19.json")).resolve()
    if not source.exists():
        source = prepared / "signals.json"
    output = (args.output_signals or (prepared / "signals.json")).resolve()
    signals = derive_v21(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        dealing_range_minutes=args.dealing_range_minutes,
        structural_leg_lookback_minutes=args.structural_leg_lookback_minutes,
        formation_expiry_minutes=args.formation_expiry_minutes,
        failure_expiry_minutes=args.failure_expiry_minutes,
        retest_expiry_minutes=args.retest_expiry_minutes,
        stop_buffer_atr=args.stop_buffer_atr,
    )
    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v21-failed-fvg-auction-retest",
                "signals": len(signals),
                "manifest": str(args.output_manifest.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
