#!/usr/bin/env python3
"""Derive causal FVG-retracement defense entries from frozen V19 states.

V19 showed that market entry immediately after measured acceptance was not
portable across the three fixed BTC weeks. V20 changes the entry sequence,
rather than tuning V19 thresholds:

1. Keep only V19 states which already describe a directional auction outcome.
2. Require a same-direction three-candle fair-value gap (FVG) left by the
   displacement leg. The gap must still be unfilled when known.
3. Do not chase. Wait for the first causal retracement into the gap.
4. Enter only after a completed defense candle closes back through the gap
   midpoint in the intended direction.
5. Invalidate on a completed close through the far edge. The initial stop is
   beyond the defended candle/FVG far edge with the already-frozen 0.20 ATR
   buffer.
6. Continuations target the pre-event 240-minute external liquidity. Reversals
   target pre-event equilibrium first. If no predeclared objective remains
   ahead of entry, emit no trade.

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

SOURCE_STATES = frozenset(
    {
        "MEASURED_ACCEPTANCE_CONTINUATION",
        "EXECUTED_FLOW_VACUUM_CONTINUATION",
        "EXECUTED_FLOW_ABSORPTION_CHOCH_REVERSAL",
    }
)
CONTINUATION_STATE = "FVG_RETRACE_DEFENSE_CONTINUATION"
REVERSAL_STATE = "FVG_RETRACE_DEFENSE_REVERSAL"


@dataclass(frozen=True, slots=True)
class FairValueGap:
    direction: int
    formed_minute: int
    lower: float
    upper: float

    @property
    def midpoint(self) -> float:
        return (self.lower + self.upper) / 2.0


def _complete_rows(
    values: dict[int, dict[str, float]],
    start: int,
    end: int,
) -> list[dict[str, float]] | None:
    rows = [values.get(minute) for minute in range(start, end)]
    if any(row is None for row in rows):
        return None
    return [row for row in rows if row is not None]


def directional_fvg(
    first: dict[str, float],
    middle: dict[str, float],
    third: dict[str, float],
    *,
    direction: int,
    formed_minute: int,
) -> FairValueGap | None:
    """Return a directional three-candle FVG, with no fitted size threshold."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if direction > 0:
        lower = float(first["high"])
        upper = float(third["low"])
        directional_body = float(middle["close"]) > float(middle["open"])
        if not directional_body or upper <= lower:
            return None
    else:
        lower = float(third["high"])
        upper = float(first["low"])
        directional_body = float(middle["close"]) < float(middle["open"])
        if not directional_body or upper <= lower:
            return None
    if not all(math.isfinite(value) and value > 0.0 for value in (lower, upper)):
        return None
    return FairValueGap(
        direction=direction,
        formed_minute=formed_minute,
        lower=lower,
        upper=upper,
    )


def gap_fully_mitigated(
    futures: dict[int, dict[str, float]],
    *,
    gap: FairValueGap,
    start_minute: int,
    end_minute: int,
) -> bool | None:
    """Return whether the far edge was traded through before `end_minute`.

    Missing minutes return None and invalidate the causal sequence.
    """
    for minute in range(start_minute, end_minute + 1):
        row = futures.get(minute)
        if row is None:
            return None
        if gap.direction > 0 and float(row["low"]) <= gap.lower:
            return True
        if gap.direction < 0 and float(row["high"]) >= gap.upper:
            return True
    return False


def find_active_displacement_fvg(
    futures: dict[int, dict[str, float]],
    *,
    direction: int,
    structural_start_minute: int,
    known_minute: int,
    formation_expiry_minutes: int,
) -> tuple[FairValueGap | None, str]:
    """Find the latest unfilled FVG already known, else the first immediate one.

    A future FVG is admitted only within the fixed immediate formation window
    after the source state. Its candle must complete before any later decision.
    """
    if formation_expiry_minutes < 0:
        raise ValueError("formation_expiry_minutes must be non-negative")
    search_start = min(structural_start_minute, known_minute)
    latest_active: FairValueGap | None = None
    for minute in range(search_start + 2, known_minute + 1):
        rows = [futures.get(value) for value in (minute - 2, minute - 1, minute)]
        if any(row is None for row in rows):
            return None, "MISSING_DISPLACEMENT_MINUTE"
        gap = directional_fvg(
            rows[0], rows[1], rows[2],  # type: ignore[arg-type]
            direction=direction,
            formed_minute=minute,
        )
        if gap is None:
            continue
        mitigated = gap_fully_mitigated(
            futures,
            gap=gap,
            start_minute=minute + 1,
            end_minute=known_minute,
        )
        if mitigated is None:
            return None, "MISSING_DISPLACEMENT_MINUTE"
        if not mitigated:
            latest_active = gap
    if latest_active is not None:
        return latest_active, "ACTIVE_FVG_FROM_STRUCTURAL_LEG"

    for minute in range(known_minute + 1, known_minute + formation_expiry_minutes + 1):
        rows = [futures.get(value) for value in (minute - 2, minute - 1, minute)]
        if any(row is None for row in rows):
            return None, "MISSING_IMMEDIATE_FORMATION_MINUTE"
        gap = directional_fvg(
            rows[0], rows[1], rows[2],  # type: ignore[arg-type]
            direction=direction,
            formed_minute=minute,
        )
        if gap is not None:
            return gap, "IMMEDIATE_POST_CONFIRMATION_FVG"
    return None, "NO_DIRECTIONAL_DISPLACEMENT_FVG"


def find_retrace_defense(
    futures: dict[int, dict[str, float]],
    *,
    gap: FairValueGap,
    start_minute: int,
    expiry_minutes: int,
) -> tuple[int, dict[str, float], int] | tuple[None, None, str]:
    """Wait for a completed FVG touch-and-defense or completed invalidation."""
    if expiry_minutes <= 0:
        raise ValueError("expiry_minutes must be positive")
    touches = 0
    for offset in range(expiry_minutes):
        minute = start_minute + offset
        row = futures.get(minute)
        if row is None:
            return None, None, "MISSING_RETRACE_MINUTE"
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        if gap.direction > 0 and close <= gap.lower:
            return None, None, "FVG_FAR_EDGE_CLOSE_INVALIDATION"
        if gap.direction < 0 and close >= gap.upper:
            return None, None, "FVG_FAR_EDGE_CLOSE_INVALIDATION"
        touched = low <= gap.upper and high >= gap.lower
        if not touched:
            continue
        touches += 1
        defended = (
            close > gap.midpoint and close > open_price
            if gap.direction > 0
            else close < gap.midpoint and close < open_price
        )
        if defended:
            return minute, row, touches
    return None, None, "FVG_RETRACE_UNRESOLVED"


def _nearest_confirmed_pivot_ahead(
    futures: dict[int, dict[str, float]],
    *,
    start_minute: int,
    end_minute: int,
    direction: int,
    entry_reference: float,
    final_target: float,
) -> float | None:
    """Return the nearest price-side pivot confirmed before entry."""
    candidates: list[float] = []
    for minute in range(start_minute + 1, end_minute - 1):
        left = futures.get(minute - 1)
        center = futures.get(minute)
        right = futures.get(minute + 1)
        if left is None or center is None or right is None:
            return None
        if direction > 0:
            value = float(center["high"])
            if (
                value > float(left["high"])
                and value >= float(right["high"])
                and entry_reference < value < final_target
            ):
                candidates.append(value)
        else:
            value = float(center["low"])
            if (
                value < float(left["low"])
                and value <= float(right["low"])
                and final_target < value < entry_reference
            ):
                candidates.append(value)
    if not candidates:
        return None
    return min(candidates, key=lambda value: direction * (value - entry_reference))


def derive_v20(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
    dealing_range_minutes: int = 240,
    structural_leg_lookback_minutes: int = 120,
    formation_expiry_minutes: int = 3,
    retrace_expiry_minutes: int = 240,
    stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if dealing_range_minutes <= 0 or structural_leg_lookback_minutes <= 0:
        raise ValueError("lookbacks must be positive")
    if formation_expiry_minutes < 0 or retrace_expiry_minutes <= 0:
        raise ValueError("formation/retrace expiry invalid")
    if stop_buffer_atr < 0.0:
        raise ValueError("stop_buffer_atr must be non-negative")

    futures = load_futures_minutes(raw_root)
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    derived_by_key: dict[tuple[int, int, str], dict[str, Any]] = {}
    no_trade_reasons: dict[str, int] = {}
    source_state_counts: dict[str, int] = {}
    output_state_counts: dict[str, int] = {}
    formation_modes: dict[str, int] = {}
    retrace_waits: list[int] = []
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
        direction = int(original["direction"])
        if direction not in (-1, 1):
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
            direction=direction,
            structural_start_minute=structural_start,
            known_minute=known_minute,
            formation_expiry_minutes=formation_expiry_minutes,
        )
        if gap is None:
            count(no_trade_reasons, formation_mode)
            continue
        count(formation_modes, formation_mode)

        defense_start = max(known_minute + 1, gap.formed_minute + 1)
        defense = find_retrace_defense(
            futures,
            gap=gap,
            start_minute=defense_start,
            expiry_minutes=retrace_expiry_minutes,
        )
        if defense[0] is None:
            count(no_trade_reasons, str(defense[2]))
            continue
        defense_minute = int(defense[0])
        defense_row = defense[1]
        touches = int(defense[2])
        assert isinstance(defense_row, dict)

        prior = _complete_rows(
            futures,
            event_start_minute - dealing_range_minutes,
            event_start_minute,
        )
        if prior is None or len(prior) != dealing_range_minutes:
            count(no_trade_reasons, "MISSING_PRE_EVENT_DEALING_RANGE")
            continue
        dealing_low = min(float(row["low"]) for row in prior)
        dealing_high = max(float(row["high"]) for row in prior)
        dealing_equilibrium = (dealing_low + dealing_high) / 2.0
        entry_kind = str(original.get("entry_kind", "CONTINUATION")).upper()
        entry_reference = float(defense_row["close"])

        if entry_kind == "REVERSAL":
            target = dealing_equilibrium
            if direction * (target - entry_reference) <= 0.0:
                target = dealing_high if direction > 0 else dealing_low
        else:
            target = dealing_high if direction > 0 else dealing_low
        if (
            not math.isfinite(target)
            or direction * (target - entry_reference) <= 0.0
        ):
            count(no_trade_reasons, "NO_PREDECLARED_LIQUIDITY_OBJECTIVE_AHEAD")
            continue

        far_edge = gap.lower if direction > 0 else gap.upper
        defense_extreme = (
            float(defense_row["low"])
            if direction > 0
            else float(defense_row["high"])
        )
        stop_anchor = (
            min(far_edge, defense_extreme)
            if direction > 0
            else max(far_edge, defense_extreme)
        )
        stop = stop_anchor - direction * stop_buffer_atr * atr
        risk_distance = direction * (entry_reference - stop)
        if not math.isfinite(stop) or risk_distance <= 0.0:
            count(no_trade_reasons, "NON_EXECUTABLE_FVG_INVALIDATION")
            continue
        reward_distance = direction * (target - entry_reference)
        gross_rr = reward_distance / risk_distance

        waypoint = _nearest_confirmed_pivot_ahead(
            futures,
            start_minute=event_start_minute - dealing_range_minutes,
            end_minute=event_start_minute,
            direction=direction,
            entry_reference=entry_reference,
            final_target=target,
        )
        confirm_ns = (defense_minute + 1) * NS_PER_MINUTE
        output_state = (
            REVERSAL_STATE if entry_kind == "REVERSAL" else CONTINUATION_STATE
        )
        details = dict(original.get("details", {}))
        details.update(
            {
                "scenario_kind": output_state,
                "source_v19_scenario_id": original["scenario_id"],
                "source_v19_state": source_state,
                "source_v19_confirm_time_ns": source_confirm_ns,
                "entry_sequence": [
                    "V19_DIRECTIONAL_AUCTION_STATE",
                    "DIRECTIONAL_DISPLACEMENT_FVG",
                    "FIRST_RETRACE_INTO_FVG",
                    "COMPLETED_MIDPOINT_DEFENSE",
                    "NEXT_NATIVE_QUOTE_ENTRY",
                ],
                "formation_mode": formation_mode,
                "fvg_formed_minute": gap.formed_minute,
                "fvg_lower": gap.lower,
                "fvg_upper": gap.upper,
                "fvg_midpoint": gap.midpoint,
                "formation_expiry_minutes": formation_expiry_minutes,
                "retrace_expiry_minutes": retrace_expiry_minutes,
                "retrace_wait_minutes": defense_minute - gap.formed_minute,
                "touch_count_before_defense": touches,
                "defense_minute": defense_minute,
                "defense_open": float(defense_row["open"]),
                "defense_high": float(defense_row["high"]),
                "defense_low": float(defense_row["low"]),
                "defense_close": entry_reference,
                "stop_anchor": stop_anchor,
                "stop_buffer_atr": stop_buffer_atr,
                "dealing_range_minutes": dealing_range_minutes,
                "dealing_range_low": dealing_low,
                "dealing_range_high": dealing_high,
                "dealing_range_equilibrium": dealing_equilibrium,
                "structural_target": target,
                "structural_waypoint": waypoint,
                "gross_structural_rr_at_defense_close": gross_rr,
            }
        )
        signal = dict(original)
        suffix = str(original["scenario_id"]).rsplit("-", 1)[-1]
        signal["scenario_id"] = f"NT-LVCFR-V20-{output_state}-{suffix}"
        signal["scenario_kind"] = output_state
        signal["confirm_time_ns"] = confirm_ns
        signal["eligible_time_ns"] = confirm_ns
        signal["initial_stop"] = stop
        signal["structural_target"] = target
        signal["target_mode"] = "STRUCTURAL_LIQUIDITY_OBJECTIVE"
        signal["disable_rapid_failure_reversal"] = True
        if waypoint is not None:
            signal["structural_protection_trigger"] = waypoint
        else:
            signal.pop("structural_protection_trigger", None)
        signal["details"] = details

        key = (confirm_ns, direction, entry_kind)
        if key in derived_by_key:
            deduplicated += 1
            continue
        derived_by_key[key] = signal
        count(output_state_counts, output_state)
        retrace_waits.append(defense_minute - gap.formed_minute)
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
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v20-fvg-retrace-defense",
        "engine_status": "causal_schedule_only_no_backtest",
        "source_signal_count": len(source),
        "derived_signal_count": len(derived),
        "source_states_routed": sorted(SOURCE_STATES),
        "source_state_counts": dict(sorted(source_state_counts.items())),
        "output_state_counts": dict(sorted(output_state_counts.items())),
        "formation_modes": dict(sorted(formation_modes.items())),
        "no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        "deduplicated_same_defense_opportunities": deduplicated,
        "dealing_range_minutes": dealing_range_minutes,
        "structural_leg_lookback_minutes": structural_leg_lookback_minutes,
        "formation_expiry_minutes": formation_expiry_minutes,
        "retrace_expiry_minutes": retrace_expiry_minutes,
        "stop_buffer_atr": stop_buffer_atr,
        "minimum_retrace_wait_minutes": min(retrace_waits) if retrace_waits else None,
        "maximum_retrace_wait_minutes": max(retrace_waits) if retrace_waits else None,
        "minimum_gross_structural_rr": min(gross_rrs) if gross_rrs else None,
        "median_gross_structural_rr": (
            sorted(gross_rrs)[len(gross_rrs) // 2] if gross_rrs else None
        ),
        "selection_policy": (
            "fixed structural sequence; no return-fit threshold search; "
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
    parser.add_argument("--retrace-expiry-minutes", type=int, default=240)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    source = (args.source_signals or (prepared / "signals-v19.json")).resolve()
    if not source.exists():
        source = prepared / "signals.json"
    output = (args.output_signals or (prepared / "signals.json")).resolve()
    signals = derive_v20(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        dealing_range_minutes=args.dealing_range_minutes,
        structural_leg_lookback_minutes=args.structural_leg_lookback_minutes,
        formation_expiry_minutes=args.formation_expiry_minutes,
        retrace_expiry_minutes=args.retrace_expiry_minutes,
        stop_buffer_atr=args.stop_buffer_atr,
    )
    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v20-fvg-retrace-defense",
                "signals": len(signals),
                "manifest": str(args.output_manifest.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
