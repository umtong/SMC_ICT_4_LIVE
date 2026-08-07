#!/usr/bin/env python3
"""Derive five-minute liquidity sweeps with one-minute precision entries.

V24 preserves a higher-timeframe liquidity narrative while moving confirmation,
entry and invalidation to the one-minute auction where risk can remain local:

1. A completed five-minute bar sweeps and reclaims an external level from the
   preceding sixty minutes; spot does not accept outside its own boundary.
2. After the sweep close, one-minute futures break the latest pivot that was
   already confirmed before the sweep began. The break must displace by at least
   the causal median one-minute body and spot aggressive flow must agree.
3. The break creates a reversal-direction one-minute FVG on the CHoCH bar or one
   of the next two completed minutes.
4. A completed one-minute retrace defends the FVG midpoint. Entry occurs on the
   next native quote.
5. Invalidation is local: beyond the FVG/retest extreme plus 0.20 one-minute
   ATR, rather than beyond the original sixty-minute sweep. The opposite side of
   the pre-sweep sixty-minute range is the liquidity objective.

This module produces only causal signals. NautilusTrader remains responsible for
orders, fills, costs, funding, positions and NAV.
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
from nt_lvcfr_data import NS_PER_MINUTE, MinuteFact, load_kline_minutes, load_open_interest

LIQUIDATION_MICRO_REVERSAL = "LIQUIDATION_SWEEP_MICRO_CHOCH_FVG_REVERSAL"
FAILED_BREAKOUT_MICRO_REVERSAL = "FAILED_BREAKOUT_MICRO_CHOCH_FVG_REVERSAL"


@dataclass(frozen=True, slots=True)
class OneBar:
    start_minute: int
    open: float
    high: float
    low: float
    close: float
    notional: float
    signed_notional: float

    @property
    def end_minute(self) -> int:
        return self.start_minute + 1

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def flow(self) -> float:
        return self.signed_notional / self.notional if self.notional > 0.0 else 0.0

    def as_dict(self) -> dict[str, float]:
        return {"open": self.open, "high": self.high, "low": self.low, "close": self.close}


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


def one_bar(fact: MinuteFact) -> OneBar:
    return OneBar(
        start_minute=fact.minute_index,
        open=fact.open,
        high=fact.high,
        low=fact.low,
        close=fact.close,
        notional=fact.notional,
        signed_notional=fact.signed_notional,
    )


def aggregate_five(minutes: Sequence[MinuteFact]) -> dict[int, FiveBar]:
    grouped: dict[int, list[MinuteFact]] = {}
    for bar in minutes:
        end_minute = (bar.minute_index // 5 + 1) * 5
        grouped.setdefault(end_minute, []).append(bar)
    output: dict[int, FiveBar] = {}
    for end_minute, members in sorted(grouped.items()):
        if [item.minute_index for item in members] != list(range(end_minute - 5, end_minute)):
            continue
        output[end_minute] = FiveBar(
            end_minute=end_minute,
            open=members[0].open,
            high=max(item.high for item in members),
            low=min(item.low for item in members),
            close=members[-1].close,
            notional=sum(item.notional for item in members),
            signed_notional=sum(item.signed_notional for item in members),
        )
    return output


def rolling_atr(bars: Sequence[OneBar], window: int) -> dict[int, float]:
    if window <= 0:
        raise ValueError("window must be positive")
    history: deque[float] = deque(maxlen=window)
    previous_close: float | None = None
    output: dict[int, float] = {}
    for bar in bars:
        true_range = bar.high - bar.low
        if previous_close is not None:
            true_range = max(true_range, abs(bar.high - previous_close), abs(bar.low - previous_close))
        history.append(true_range)
        if len(history) == window:
            output[bar.end_minute] = sum(history) / window
        previous_close = bar.close
    return output


def sweep_reversal_direction(current: FiveBar, *, prior_high: float, prior_low: float) -> int:
    swept_high = current.high > prior_high and current.close < prior_high
    swept_low = current.low < prior_low and current.close > prior_low
    if swept_high == swept_low:
        return 0
    return -1 if swept_high else 1


def latest_confirmed_pivot(
    bars: Sequence[OneBar],
    *,
    before_index: int,
    direction: int,
    lookback_bars: int,
    pivot_span: int,
) -> tuple[int, float] | None:
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
            level = bars[center].low
            if level == min(item.low for item in window):
                return center, level
        else:
            level = bars[center].high
            if level == max(item.high for item in window):
                return center, level
    return None


def find_micro_choch_fvg(
    futures: Sequence[OneBar],
    spot: Sequence[OneBar],
    *,
    start_index: int,
    direction: int,
    pivot_level: float,
    choch_expiry_minutes: int,
    displacement_baseline_minutes: int,
    fvg_formation_minutes: int,
) -> tuple[int, FairValueGap, float] | tuple[None, None, str]:
    last = min(len(futures), start_index + choch_expiry_minutes)
    for index in range(start_index, last):
        bar = futures[index]
        broke = bar.close < pivot_level if direction < 0 else bar.close > pivot_level
        if not broke:
            continue
        baseline = futures[max(0, index - displacement_baseline_minutes) : index]
        if not baseline:
            return None, None, "MISSING_MICRO_DISPLACEMENT_BASELINE"
        median_body = median(item.body for item in baseline)
        if bar.body < median_body:
            continue
        if direction * spot[index].flow <= 0.0:
            continue
        formation_end = min(len(futures), index + fvg_formation_minutes)
        for formed_index in range(index, formation_end):
            if formed_index < 2:
                continue
            formed = futures[formed_index]
            if direction < 0 and formed.close >= pivot_level:
                continue
            if direction > 0 and formed.close <= pivot_level:
                continue
            gap = directional_fvg(
                futures[formed_index - 2].as_dict(),
                futures[formed_index - 1].as_dict(),
                formed.as_dict(),
                direction=direction,
                formed_minute=formed.end_minute,
            )
            if gap is not None:
                return formed_index, gap, median_body
    return None, None, "MICRO_CHOCH_OR_FVG_UNRESOLVED"


def find_micro_fvg_defense(
    bars: Sequence[OneBar],
    *,
    gap: FairValueGap,
    start_index: int,
    expiry_minutes: int,
) -> tuple[int, OneBar, int] | tuple[None, None, str]:
    touches = 0
    last = min(len(bars), start_index + expiry_minutes)
    for index in range(start_index, last):
        bar = bars[index]
        if gap.direction > 0 and bar.close <= gap.lower:
            return None, None, "MICRO_FVG_FAR_EDGE_INVALIDATION"
        if gap.direction < 0 and bar.close >= gap.upper:
            return None, None, "MICRO_FVG_FAR_EDGE_INVALIDATION"
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
    return None, None, "MICRO_FVG_DEFENSE_UNRESOLVED"


def derive_v24(
    *,
    prepared_root: Path,
    output_signals: Path,
    output_manifest: Path,
    range_lookback_five_bars: int = 12,
    pivot_lookback_minutes: int = 30,
    pivot_span: int = 2,
    choch_expiry_minutes: int = 10,
    displacement_baseline_minutes: int = 20,
    fvg_formation_minutes: int = 3,
    retrace_expiry_minutes: int = 10,
    atr_minutes: int = 20,
    stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if range_lookback_five_bars <= 0:
        raise ValueError("range lookback must be positive")
    if pivot_lookback_minutes <= 2 * pivot_span:
        raise ValueError("pivot lookback too short")
    if stop_buffer_atr < 0.0:
        raise ValueError("stop_buffer_atr must be non-negative")

    raw_root = prepared_root / "raw"
    futures_facts = load_kline_minutes(sorted((raw_root / "futures_kline").glob("*.zip")))
    spot_facts = load_kline_minutes(sorted((raw_root / "spot_kline").glob("*.zip")))
    oi = load_open_interest(sorted((raw_root / "open_interest").glob("*.zip")))
    futures_fact_map = {item.minute_index: item for item in futures_facts}
    spot_fact_map = {item.minute_index: item for item in spot_facts}
    aligned_starts = sorted(set(futures_fact_map) & set(spot_fact_map))
    futures = [one_bar(futures_fact_map[minute]) for minute in aligned_starts]
    spot = [one_bar(spot_fact_map[minute]) for minute in aligned_starts]
    index_by_start = {bar.start_minute: index for index, bar in enumerate(futures)}
    futures_five_map = aggregate_five(futures_facts)
    spot_five_map = aggregate_five(spot_facts)
    five_minutes = sorted(set(futures_five_map) & set(spot_five_map))
    futures_five = [futures_five_map[minute] for minute in five_minutes]
    spot_five = [spot_five_map[minute] for minute in five_minutes]
    atr = rolling_atr(futures, atr_minutes)

    data_manifest = json.loads((prepared_root / "data_manifest.json").read_text(encoding="utf-8"))
    evaluation_start_ns = int(data_manifest["evaluation_start_ns"])
    evaluation_end_ns = int(data_manifest["evaluation_end_ns"])

    signals: list[dict[str, Any]] = []
    state_counts: dict[str, int] = {}
    no_trade_reasons: dict[str, int] = {}
    sweep_counts = {"HIGH": 0, "LOW": 0}
    gross_rrs: list[float] = []
    cooldown_until_start_minute = -1

    def count(mapping: dict[str, int], key: str) -> None:
        mapping[key] = mapping.get(key, 0) + 1

    for five_index in range(range_lookback_five_bars, len(futures_five)):
        context = futures_five[five_index]
        context_end_ns = context.end_minute * NS_PER_MINUTE
        if not evaluation_start_ns <= context_end_ns < evaluation_end_ns:
            continue
        if context.end_minute <= cooldown_until_start_minute:
            continue
        prior = futures_five[five_index - range_lookback_five_bars : five_index]
        prior_spot = spot_five[five_index - range_lookback_five_bars : five_index]
        prior_high = max(item.high for item in prior)
        prior_low = min(item.low for item in prior)
        prior_midpoint = (prior_low + prior_high) / 2.0
        direction = sweep_reversal_direction(context, prior_high=prior_high, prior_low=prior_low)
        if direction == 0:
            continue
        context_spot = spot_five[five_index]
        if direction < 0:
            sweep_counts["HIGH"] += 1
            spot_external = max(item.high for item in prior_spot)
            if context_spot.close >= spot_external:
                count(no_trade_reasons, "SPOT_ACCEPTED_ABOVE_EXTERNAL_HIGH")
                continue
        else:
            sweep_counts["LOW"] += 1
            spot_external = min(item.low for item in prior_spot)
            if context_spot.close <= spot_external:
                count(no_trade_reasons, "SPOT_ACCEPTED_BELOW_EXTERNAL_LOW")
                continue

        context_start_minute = context.end_minute - 5
        before_index = index_by_start.get(context_start_minute)
        start_index = index_by_start.get(context.end_minute)
        if before_index is None or start_index is None:
            count(no_trade_reasons, "MISSING_MICRO_CONTEXT_ALIGNMENT")
            continue
        pivot = latest_confirmed_pivot(
            futures,
            before_index=before_index,
            direction=direction,
            lookback_bars=pivot_lookback_minutes,
            pivot_span=pivot_span,
        )
        if pivot is None:
            count(no_trade_reasons, "NO_PRE_SWEEP_CONFIRMED_MICRO_PIVOT")
            continue
        pivot_index, pivot_level = pivot
        choch = find_micro_choch_fvg(
            futures,
            spot,
            start_index=start_index,
            direction=direction,
            pivot_level=pivot_level,
            choch_expiry_minutes=choch_expiry_minutes,
            displacement_baseline_minutes=displacement_baseline_minutes,
            fvg_formation_minutes=fvg_formation_minutes,
        )
        if choch[0] is None:
            count(no_trade_reasons, str(choch[2]))
            continue
        formed_index = int(choch[0])
        gap = choch[1]
        median_body = float(choch[2])
        assert isinstance(gap, FairValueGap)
        defense = find_micro_fvg_defense(
            futures,
            gap=gap,
            start_index=formed_index + 1,
            expiry_minutes=retrace_expiry_minutes,
        )
        if defense[0] is None:
            count(no_trade_reasons, str(defense[2]))
            continue
        defense_index = int(defense[0])
        defense_bar = defense[1]
        touches = int(defense[2])
        assert isinstance(defense_bar, OneBar)

        at = atr.get(defense_bar.end_minute)
        if at is None or not math.isfinite(at) or at <= 0.0:
            count(no_trade_reasons, "MISSING_MICRO_ATR")
            continue
        entry_reference = defense_bar.close
        target = prior_high if direction > 0 else prior_low
        if direction * (target - entry_reference) <= 0.0:
            count(no_trade_reasons, "OPPOSITE_EXTERNAL_TARGET_NOT_AHEAD")
            continue
        if direction > 0:
            stop_anchor = min(gap.lower, defense_bar.low)
        else:
            stop_anchor = max(gap.upper, defense_bar.high)
        stop = stop_anchor - direction * stop_buffer_atr * at
        risk_distance = direction * (entry_reference - stop)
        reward_distance = direction * (target - entry_reference)
        if risk_distance <= 0.0 or not math.isfinite(stop):
            count(no_trade_reasons, "NON_EXECUTABLE_LOCAL_INVALIDATION")
            continue
        gross_rr = reward_distance / risk_distance
        waypoint = (
            prior_midpoint
            if direction * (prior_midpoint - entry_reference) > 0.0
            and direction * (target - prior_midpoint) >= 0.0
            else None
        )

        sweep_start_ns = context_start_minute * NS_PER_MINUTE
        choch_end_ns = futures[formed_index].end_minute * NS_PER_MINUTE
        confirm_ns = defense_bar.end_minute * NS_PER_MINUTE
        oi_before = oi.get(sweep_start_ns)
        oi_after = oi.get(choch_end_ns)
        oi_change_bp: float | None = None
        if oi_before is not None and oi_after is not None and oi_before > 0.0:
            oi_change_bp = (oi_after / oi_before - 1.0) * 10_000.0
        state = (
            LIQUIDATION_MICRO_REVERSAL
            if oi_change_bp is not None and oi_change_bp < 0.0
            else FAILED_BREAKOUT_MICRO_REVERSAL
        )
        suffix = sha256(
            f"{confirm_ns}|{direction}|{stop_anchor:.12g}|{pivot_level:.12g}".encode()
        ).hexdigest()[:16]
        details = {
            "scenario_kind": state,
            "entry_kind": "REVERSAL",
            "entry_sequence": [
                "PRECEDING_60M_EXTERNAL_LIQUIDITY",
                "FIVE_MINUTE_SWEEP_AND_RECLAIM",
                "SPOT_EXTERNAL_NON_ACCEPTANCE",
                "PRE_SWEEP_CONFIRMED_ONE_MINUTE_PIVOT",
                "ONE_MINUTE_CHOCH_AND_SPOT_FLOW_DISPLACEMENT",
                "ONE_MINUTE_FVG_RETRACE_DEFENSE",
                "NEXT_NATIVE_QUOTE_ENTRY",
            ],
            "range_lookback_five_bars": range_lookback_five_bars,
            "prior_range_low": prior_low,
            "prior_range_high": prior_high,
            "prior_range_midpoint": prior_midpoint,
            "sweep_side": "LOW" if direction > 0 else "HIGH",
            "sweep_start_time_ns": sweep_start_ns,
            "sweep_end_time_ns": context_end_ns,
            "sweep_high": context.high,
            "sweep_low": context.low,
            "micro_pivot_index": pivot_index,
            "micro_pivot_level": pivot_level,
            "choch_fvg_end_time_ns": choch_end_ns,
            "displacement_median_body": median_body,
            "choch_fvg_bar_body": futures[formed_index].body,
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
            "stop_anchor": stop_anchor,
            "stop_buffer_atr": stop_buffer_atr,
            "structural_target": target,
            "structural_waypoint": waypoint,
            "gross_structural_rr_at_defense": gross_rr,
        }
        signal: dict[str, Any] = {
            "scenario_id": f"NT-LVCFR-V24-{state}-{suffix}",
            "scenario_kind": state,
            "entry_kind": "REVERSAL",
            "confirm_time_ns": confirm_ns,
            "eligible_time_ns": confirm_ns,
            "direction": direction,
            "initial_stop": stop,
            "atr": at,
            "first_start_time_ns": sweep_start_ns,
            "first_end_time_ns": context_end_ns,
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
        cooldown_until_start_minute = defense_bar.end_minute

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(json.dumps(signals, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ordered_rrs = sorted(gross_rrs)
    evaluation_days = (evaluation_end_ns - evaluation_start_ns) / (86_400 * 1_000_000_000)
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v24-mtf-sweep-micro-entry",
        "engine_status": "independent_causal_schedule_only_no_backtest",
        "derived_signal_count": len(signals),
        "evaluation_days": evaluation_days,
        "signals_per_day": len(signals) / evaluation_days if evaluation_days > 0.0 else 0.0,
        "output_state_counts": dict(sorted(state_counts.items())),
        "sweep_counts": sweep_counts,
        "no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        "range_lookback_five_bars": range_lookback_five_bars,
        "pivot_lookback_minutes": pivot_lookback_minutes,
        "pivot_span": pivot_span,
        "choch_expiry_minutes": choch_expiry_minutes,
        "displacement_baseline_minutes": displacement_baseline_minutes,
        "fvg_formation_minutes": fvg_formation_minutes,
        "retrace_expiry_minutes": retrace_expiry_minutes,
        "atr_minutes": atr_minutes,
        "stop_buffer_atr": stop_buffer_atr,
        "minimum_gross_structural_rr": min(gross_rrs) if gross_rrs else None,
        "median_gross_structural_rr": ordered_rrs[len(ordered_rrs) // 2] if ordered_rrs else None,
        "selection_policy": (
            "fixed 5m liquidity context and 1m causal entry sequence; local micro invalidation; "
            "no return-fit threshold search; native cost-feasibility gate required"
        ),
        "output_signals": str(output_signals),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return signals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-signals", type=Path)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--range-lookback-five-bars", type=int, default=12)
    parser.add_argument("--pivot-lookback-minutes", type=int, default=30)
    parser.add_argument("--pivot-span", type=int, default=2)
    parser.add_argument("--choch-expiry-minutes", type=int, default=10)
    parser.add_argument("--displacement-baseline-minutes", type=int, default=20)
    parser.add_argument("--fvg-formation-minutes", type=int, default=3)
    parser.add_argument("--retrace-expiry-minutes", type=int, default=10)
    parser.add_argument("--atr-minutes", type=int, default=20)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    output = (args.output_signals or (prepared / "signals.json")).resolve()
    signals = derive_v24(
        prepared_root=prepared,
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        range_lookback_five_bars=args.range_lookback_five_bars,
        pivot_lookback_minutes=args.pivot_lookback_minutes,
        pivot_span=args.pivot_span,
        choch_expiry_minutes=args.choch_expiry_minutes,
        displacement_baseline_minutes=args.displacement_baseline_minutes,
        fvg_formation_minutes=args.fvg_formation_minutes,
        retrace_expiry_minutes=args.retrace_expiry_minutes,
        atr_minutes=args.atr_minutes,
        stop_buffer_atr=args.stop_buffer_atr,
    )
    print(json.dumps({"candidate": "candidate-03-nt-lvcfr-v24-mtf-sweep-micro-entry", "signals": len(signals)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
