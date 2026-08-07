#!/usr/bin/env python3
"""Derive recurring external-sweep/CHoCH/FVG-retest reversals.

V22 is an independent source detector rather than another filter on the sparse
V19 liquidation schedule. It converts a recurring auction sequence into causal
rules on completed five-minute bars:

1. A bar trades beyond the preceding 60-minute external high or low and closes
   back inside that range (liquidity sweep/reclaim).
2. Spot does not accept beyond its corresponding external boundary.
3. Within 30 minutes, futures close through the latest already-confirmed
   opposite internal pivot (CHoCH) on at least median recent body displacement,
   while spot aggressive flow supports the reversal.
4. The displacement leaves a reversal-direction FVG, either on the CHoCH bar or
   within the next two completed bars.
5. Price retraces into that FVG and a completed candle defends its midpoint in
   the reversal direction. Entry occurs on the next native quote.
6. Invalidation remains beyond the original sweep extreme plus 0.20 ATR. The
   opposite side of the swept 60-minute range is the final liquidity objective;
   range equilibrium is a protection waypoint.

The signal schedule contains no fill, fee, PnL or NAV simulation. Those remain
NautilusTrader responsibilities.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any, Sequence

from derive_nt_lvcfr_v20_signals import FairValueGap, directional_fvg
from nt_lvcfr_data import (
    NS_PER_MINUTE,
    MinuteFact,
    load_kline_minutes,
    load_open_interest,
)

LIQUIDATION_SWEEP_REVERSAL = "LIQUIDATION_SWEEP_CHOCH_FVG_REVERSAL"
FAILED_BREAKOUT_REVERSAL = "FAILED_BREAKOUT_SWEEP_CHOCH_FVG_REVERSAL"


@dataclass(frozen=True, slots=True)
class FiveBar:
    end_minute: int
    open: float
    high: float
    low: float
    close: float
    notional: float
    signed_notional: float

    @property
    def flow(self) -> float:
        return self.signed_notional / self.notional if self.notional > 0.0 else 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    def as_dict(self) -> dict[str, float]:
        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
        }


def aggregate_five(minutes: Sequence[MinuteFact]) -> dict[int, FiveBar]:
    grouped: dict[int, list[MinuteFact]] = {}
    for bar in minutes:
        end_minute = (bar.minute_index // 5 + 1) * 5
        grouped.setdefault(end_minute, []).append(bar)
    output: dict[int, FiveBar] = {}
    for end_minute, members in sorted(grouped.items()):
        expected = list(range(end_minute - 5, end_minute))
        if [member.minute_index for member in members] != expected:
            continue
        output[end_minute] = FiveBar(
            end_minute=end_minute,
            open=members[0].open,
            high=max(member.high for member in members),
            low=min(member.low for member in members),
            close=members[-1].close,
            notional=sum(member.notional for member in members),
            signed_notional=sum(member.signed_notional for member in members),
        )
    return output


def rolling_atr(bars: Sequence[FiveBar], window: int) -> dict[int, float]:
    if window <= 0:
        raise ValueError("window must be positive")
    history: deque[float] = deque(maxlen=window)
    output: dict[int, float] = {}
    previous_close: float | None = None
    for bar in bars:
        true_range = bar.high - bar.low
        if previous_close is not None:
            true_range = max(
                true_range,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        history.append(true_range)
        if len(history) == window:
            output[bar.end_minute] = sum(history) / window
        previous_close = bar.close
    return output


def sweep_reversal_direction(
    current: FiveBar,
    *,
    prior_high: float,
    prior_low: float,
) -> int:
    """Return -1 for a reclaimed high sweep, +1 for a reclaimed low sweep."""
    swept_high = current.high > prior_high and current.close < prior_high
    swept_low = current.low < prior_low and current.close > prior_low
    if swept_high == swept_low:
        return 0
    return -1 if swept_high else 1


def latest_confirmed_internal_pivot(
    bars: Sequence[FiveBar],
    *,
    before_index: int,
    direction: int,
    lookback_bars: int,
    pivot_span: int = 2,
) -> tuple[int, float] | None:
    """Return the latest pivot known before `before_index` without lookahead."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if pivot_span <= 0 or lookback_bars <= 2 * pivot_span:
        raise ValueError("invalid pivot configuration")
    last_center = before_index - pivot_span - 1
    first_center = max(pivot_span, before_index - lookback_bars)
    for center in range(last_center, first_center - 1, -1):
        window = bars[center - pivot_span : center + pivot_span + 1]
        if len(window) != 2 * pivot_span + 1:
            continue
        if direction < 0:
            value = bars[center].low
            if value == min(bar.low for bar in window):
                return center, value
        else:
            value = bars[center].high
            if value == max(bar.high for bar in window):
                return center, value
    return None


def find_choch_fvg(
    futures: Sequence[FiveBar],
    spot: Sequence[FiveBar],
    *,
    start_index: int,
    direction: int,
    pivot_level: float,
    choch_expiry_bars: int,
    displacement_baseline_bars: int,
    fvg_formation_bars: int,
) -> tuple[int, FairValueGap, float] | tuple[None, None, str]:
    """Find a spot-supported CHoCH and its first immediate directional FVG."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    last = min(len(futures), start_index + choch_expiry_bars)
    for index in range(start_index, last):
        bar = futures[index]
        broke = bar.close < pivot_level if direction < 0 else bar.close > pivot_level
        if not broke:
            continue
        baseline = futures[max(0, index - displacement_baseline_bars) : index]
        if not baseline:
            return None, None, "MISSING_DISPLACEMENT_BASELINE"
        median_body = median(item.body for item in baseline)
        if bar.body < median_body:
            continue
        if direction * spot[index].flow <= 0.0:
            continue
        formation_end = min(len(futures), index + fvg_formation_bars)
        for formed_index in range(index, formation_end):
            if formed_index < 2:
                continue
            if direction < 0 and futures[formed_index].close >= pivot_level:
                continue
            if direction > 0 and futures[formed_index].close <= pivot_level:
                continue
            gap = directional_fvg(
                futures[formed_index - 2].as_dict(),
                futures[formed_index - 1].as_dict(),
                futures[formed_index].as_dict(),
                direction=direction,
                formed_minute=futures[formed_index].end_minute,
            )
            if gap is not None:
                return formed_index, gap, median_body
    return None, None, "CHOCH_OR_DIRECTIONAL_FVG_UNRESOLVED"


def find_fvg_retrace_defense(
    bars: Sequence[FiveBar],
    *,
    gap: FairValueGap,
    start_index: int,
    expiry_bars: int,
) -> tuple[int, FiveBar, int] | tuple[None, None, str]:
    if expiry_bars <= 0:
        raise ValueError("expiry_bars must be positive")
    touches = 0
    last = min(len(bars), start_index + expiry_bars)
    for index in range(start_index, last):
        bar = bars[index]
        if gap.direction > 0 and bar.close <= gap.lower:
            return None, None, "FVG_FAR_EDGE_CLOSE_INVALIDATION"
        if gap.direction < 0 and bar.close >= gap.upper:
            return None, None, "FVG_FAR_EDGE_CLOSE_INVALIDATION"
        touched = bar.low <= gap.upper and bar.high >= gap.lower
        if not touched:
            continue
        touches += 1
        defended = (
            bar.close > gap.midpoint and bar.close > bar.open
            if gap.direction > 0
            else bar.close < gap.midpoint and bar.close < bar.open
        )
        if defended:
            return index, bar, touches
    return None, None, "FVG_RETRACE_DEFENSE_UNRESOLVED"


def derive_v22(
    *,
    prepared_root: Path,
    output_signals: Path,
    output_manifest: Path,
    range_lookback_bars: int = 12,
    internal_pivot_lookback_bars: int = 18,
    pivot_span: int = 2,
    choch_expiry_bars: int = 6,
    displacement_baseline_bars: int = 24,
    fvg_formation_bars: int = 3,
    retrace_expiry_bars: int = 6,
    atr_bars: int = 12,
    stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if range_lookback_bars <= 2 * pivot_span:
        raise ValueError("range lookback too short")
    if stop_buffer_atr < 0.0:
        raise ValueError("stop_buffer_atr must be non-negative")

    raw_root = prepared_root / "raw"
    futures_minutes = load_kline_minutes(
        sorted((raw_root / "futures_kline").glob("*.zip"))
    )
    spot_minutes = load_kline_minutes(
        sorted((raw_root / "spot_kline").glob("*.zip"))
    )
    oi = load_open_interest(
        sorted((raw_root / "open_interest").glob("*.zip"))
    )
    futures_map = aggregate_five(futures_minutes)
    spot_map = aggregate_five(spot_minutes)
    aligned_minutes = sorted(set(futures_map) & set(spot_map))
    futures = [futures_map[minute] for minute in aligned_minutes]
    spot = [spot_map[minute] for minute in aligned_minutes]
    atr = rolling_atr(futures, atr_bars)

    data_manifest = json.loads(
        (prepared_root / "data_manifest.json").read_text(encoding="utf-8")
    )
    evaluation_start_ns = int(data_manifest["evaluation_start_ns"])
    evaluation_end_ns = int(data_manifest["evaluation_end_ns"])

    signals: list[dict[str, Any]] = []
    state_counts: dict[str, int] = {}
    no_trade_reasons: dict[str, int] = {}
    gross_rrs: list[float] = []
    sweep_counts = {"HIGH": 0, "LOW": 0}
    cooldown_until = 0

    def count(mapping: dict[str, int], key: str) -> None:
        mapping[key] = mapping.get(key, 0) + 1

    warmup = max(
        range_lookback_bars,
        internal_pivot_lookback_bars,
        displacement_baseline_bars,
        atr_bars,
    ) + pivot_span + 1
    for index in range(warmup, len(futures)):
        if index < cooldown_until:
            continue
        bar = futures[index]
        end_ns = bar.end_minute * NS_PER_MINUTE
        if not evaluation_start_ns <= end_ns < evaluation_end_ns:
            continue
        prior = futures[index - range_lookback_bars : index]
        prior_spot = spot[index - range_lookback_bars : index]
        if len(prior) != range_lookback_bars:
            continue
        prior_high = max(item.high for item in prior)
        prior_low = min(item.low for item in prior)
        prior_midpoint = (prior_low + prior_high) / 2.0
        direction = sweep_reversal_direction(
            bar,
            prior_high=prior_high,
            prior_low=prior_low,
        )
        if direction == 0:
            continue
        if direction < 0:
            sweep_counts["HIGH"] += 1
            prior_spot_external = max(item.high for item in prior_spot)
            if spot[index].close >= prior_spot_external:
                count(no_trade_reasons, "SPOT_ACCEPTED_ABOVE_EXTERNAL_HIGH")
                continue
            sweep_extreme = bar.high
        else:
            sweep_counts["LOW"] += 1
            prior_spot_external = min(item.low for item in prior_spot)
            if spot[index].close <= prior_spot_external:
                count(no_trade_reasons, "SPOT_ACCEPTED_BELOW_EXTERNAL_LOW")
                continue
            sweep_extreme = bar.low

        pivot = latest_confirmed_internal_pivot(
            futures,
            before_index=index,
            direction=direction,
            lookback_bars=internal_pivot_lookback_bars,
            pivot_span=pivot_span,
        )
        if pivot is None:
            count(no_trade_reasons, "NO_CONFIRMED_INTERNAL_PIVOT")
            continue
        pivot_index, pivot_level = pivot
        choch = find_choch_fvg(
            futures,
            spot,
            start_index=index + 1,
            direction=direction,
            pivot_level=pivot_level,
            choch_expiry_bars=choch_expiry_bars,
            displacement_baseline_bars=displacement_baseline_bars,
            fvg_formation_bars=fvg_formation_bars,
        )
        if choch[0] is None:
            count(no_trade_reasons, str(choch[2]))
            continue
        formed_index = int(choch[0])
        gap = choch[1]
        median_body = float(choch[2])
        assert isinstance(gap, FairValueGap)

        defense = find_fvg_retrace_defense(
            futures,
            gap=gap,
            start_index=formed_index + 1,
            expiry_bars=retrace_expiry_bars,
        )
        if defense[0] is None:
            count(no_trade_reasons, str(defense[2]))
            continue
        defense_index = int(defense[0])
        defense_bar = defense[1]
        touches = int(defense[2])
        assert isinstance(defense_bar, FiveBar)

        at = atr.get(defense_bar.end_minute)
        if at is None or not math.isfinite(at) or at <= 0.0:
            count(no_trade_reasons, "MISSING_CAUSAL_ATR")
            continue
        entry_reference = defense_bar.close
        target = prior_high if direction > 0 else prior_low
        if direction * (target - entry_reference) <= 0.0:
            count(no_trade_reasons, "OPPOSITE_EXTERNAL_TARGET_NOT_AHEAD")
            continue
        stop = sweep_extreme - direction * stop_buffer_atr * at
        risk_distance = direction * (entry_reference - stop)
        reward_distance = direction * (target - entry_reference)
        if risk_distance <= 0.0 or not math.isfinite(stop):
            count(no_trade_reasons, "NON_EXECUTABLE_SWEEP_INVALIDATION")
            continue
        gross_rr = reward_distance / risk_distance
        waypoint = (
            prior_midpoint
            if direction * (prior_midpoint - entry_reference) > 0.0
            and direction * (target - prior_midpoint) >= 0.0
            else None
        )

        sweep_end_ns = bar.end_minute * NS_PER_MINUTE
        choch_end_ns = futures[formed_index].end_minute * NS_PER_MINUTE
        confirm_ns = defense_bar.end_minute * NS_PER_MINUTE
        oi_before = oi.get(sweep_end_ns - 5 * NS_PER_MINUTE)
        oi_after = oi.get(choch_end_ns)
        oi_change_bp: float | None = None
        if oi_before is not None and oi_after is not None and oi_before > 0.0:
            oi_change_bp = (oi_after / oi_before - 1.0) * 10_000.0
        state = (
            LIQUIDATION_SWEEP_REVERSAL
            if oi_change_bp is not None and oi_change_bp < 0.0
            else FAILED_BREAKOUT_REVERSAL
        )
        suffix = sha256(
            f"{confirm_ns}|{direction}|{sweep_extreme:.12g}|{pivot_level:.12g}".encode()
        ).hexdigest()[:16]
        details = {
            "scenario_kind": state,
            "entry_kind": "REVERSAL",
            "entry_sequence": [
                "PRECEDING_60M_EXTERNAL_LIQUIDITY",
                "FIVE_MINUTE_SWEEP_AND_CLOSE_RECLAIM",
                "SPOT_EXTERNAL_NON_ACCEPTANCE",
                "CONFIRMED_INTERNAL_PIVOT_CHOCH",
                "SPOT_FLOW_SUPPORTED_DISPLACEMENT_FVG",
                "FVG_MIDPOINT_RETRACE_DEFENSE",
                "NEXT_NATIVE_QUOTE_ENTRY",
            ],
            "range_lookback_bars": range_lookback_bars,
            "prior_range_low": prior_low,
            "prior_range_high": prior_high,
            "prior_range_midpoint": prior_midpoint,
            "sweep_direction": "LOW" if direction > 0 else "HIGH",
            "sweep_extreme": sweep_extreme,
            "sweep_end_time_ns": sweep_end_ns,
            "internal_pivot_index": pivot_index,
            "internal_pivot_level": pivot_level,
            "choch_fvg_end_time_ns": choch_end_ns,
            "displacement_median_body": median_body,
            "choch_bar_body": futures[formed_index].body,
            "spot_choch_flow": spot[formed_index].flow,
            "fvg_lower": gap.lower,
            "fvg_upper": gap.upper,
            "fvg_midpoint": gap.midpoint,
            "fvg_retrace_touches": touches,
            "defense_end_time_ns": confirm_ns,
            "defense_open": defense_bar.open,
            "defense_high": defense_bar.high,
            "defense_low": defense_bar.low,
            "defense_close": defense_bar.close,
            "oi_change_sweep_to_choch_bp": oi_change_bp,
            "stop_anchor": sweep_extreme,
            "stop_buffer_atr": stop_buffer_atr,
            "structural_target": target,
            "structural_waypoint": waypoint,
            "gross_structural_rr_at_defense_close": gross_rr,
        }
        signal = {
            "scenario_id": f"NT-LVCFR-V22-{state}-{suffix}",
            "scenario_kind": state,
            "entry_kind": "REVERSAL",
            "confirm_time_ns": confirm_ns,
            "eligible_time_ns": confirm_ns,
            "direction": direction,
            "initial_stop": stop,
            "atr": at,
            "first_start_time_ns": (bar.end_minute - 5) * NS_PER_MINUTE,
            "first_end_time_ns": sweep_end_ns,
            "structural_target": target,
            "target_mode": "STRUCTURAL_LIQUIDITY_OBJECTIVE",
            "disable_rapid_failure_reversal": True,
            "details": details,
        }
        if waypoint is not None:
            signal["structural_protection_trigger"] = waypoint
        signals.append(signal)
        count(state_counts, state)
        gross_rrs.append(gross_rr)
        cooldown_until = defense_index + 1

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ordered_rrs = sorted(gross_rrs)
    evaluation_days = (evaluation_end_ns - evaluation_start_ns) / (1_000_000_000 * 86400)
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v22-sweep-choch-fvg-reversal",
        "engine_status": "independent_causal_schedule_only_no_backtest",
        "derived_signal_count": len(signals),
        "evaluation_days": evaluation_days,
        "signals_per_day": len(signals) / evaluation_days if evaluation_days > 0 else 0.0,
        "output_state_counts": dict(sorted(state_counts.items())),
        "sweep_counts": sweep_counts,
        "no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        "range_lookback_bars": range_lookback_bars,
        "internal_pivot_lookback_bars": internal_pivot_lookback_bars,
        "pivot_span": pivot_span,
        "choch_expiry_bars": choch_expiry_bars,
        "displacement_baseline_bars": displacement_baseline_bars,
        "fvg_formation_bars": fvg_formation_bars,
        "retrace_expiry_bars": retrace_expiry_bars,
        "atr_bars": atr_bars,
        "stop_buffer_atr": stop_buffer_atr,
        "minimum_gross_structural_rr": min(gross_rrs) if gross_rrs else None,
        "median_gross_structural_rr": (
            ordered_rrs[len(ordered_rrs) // 2] if ordered_rrs else None
        ),
        "selection_policy": (
            "fixed external-sweep/CHoCH/FVG sequence; dynamic median displacement; "
            "no return-fit threshold search; NautilusTrader execution unchanged"
        ),
        "output_signals": str(output_signals),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return signals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-signals", type=Path)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--range-lookback-bars", type=int, default=12)
    parser.add_argument("--internal-pivot-lookback-bars", type=int, default=18)
    parser.add_argument("--pivot-span", type=int, default=2)
    parser.add_argument("--choch-expiry-bars", type=int, default=6)
    parser.add_argument("--displacement-baseline-bars", type=int, default=24)
    parser.add_argument("--fvg-formation-bars", type=int, default=3)
    parser.add_argument("--retrace-expiry-bars", type=int, default=6)
    parser.add_argument("--atr-bars", type=int, default=12)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    output = (args.output_signals or (prepared / "signals.json")).resolve()
    signals = derive_v22(
        prepared_root=prepared,
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        range_lookback_bars=args.range_lookback_bars,
        internal_pivot_lookback_bars=args.internal_pivot_lookback_bars,
        pivot_span=args.pivot_span,
        choch_expiry_bars=args.choch_expiry_bars,
        displacement_baseline_bars=args.displacement_baseline_bars,
        fvg_formation_bars=args.fvg_formation_bars,
        retrace_expiry_bars=args.retrace_expiry_bars,
        atr_bars=args.atr_bars,
        stop_buffer_atr=args.stop_buffer_atr,
    )
    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v22-sweep-choch-fvg-reversal",
                "signals": len(signals),
                "manifest": str(args.output_manifest.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
