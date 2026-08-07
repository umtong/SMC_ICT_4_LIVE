#!/usr/bin/env python3
"""Derive spot-perpetual dislocation compression reversals.

V23 is an independent market-relationship detector. It trades a temporary
perpetual-futures price dislocation only after the spot/perpetual basis begins to
normalize on completed five-minute bars:

1. Compute the causal four-hour distribution of perpetual-minus-spot basis.
2. A dislocation must close outside the prior Tukey outer fence, with futures
   aggressive flow driving the deviation more strongly than spot flow.
3. Within fifteen minutes the basis must close back inside the same frozen
   fence, futures order flow must reverse, and futures price must close through
   the dislocation bar midpoint toward fair value.
4. Enter on the next native quote. Invalidate beyond the dislocation extreme
   plus 0.20 ATR. Target the static futures fair-value price implied by the
   confirmation-time spot price and the pre-event median basis.

No return labels, PnL, fills or NAV calculations occur in this module.
NautilusTrader remains the execution and accounting engine.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence

from nt_lvcfr_data import NS_PER_MINUTE, MinuteFact, load_kline_minutes, load_open_interest

OI_EXPANSION_DISLOCATION_REVERSAL = "OI_EXPANSION_BASIS_DISLOCATION_REVERSAL"
OI_CONTRACTION_DISLOCATION_REVERSAL = "OI_CONTRACTION_BASIS_DISLOCATION_REVERSAL"
UNKNOWN_INVENTORY_DISLOCATION_REVERSAL = "BASIS_DISLOCATION_REVERSAL"


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


@dataclass(frozen=True, slots=True)
class BasisBar:
    end_minute: int
    futures: FiveBar
    spot: FiveBar
    basis_bp: float


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


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def tukey_fences(values: Sequence[float]) -> tuple[float, float, float, float, float]:
    q1 = _quantile(values, 0.25)
    q3 = _quantile(values, 0.75)
    med = _quantile(values, 0.50)
    iqr = q3 - q1
    if not all(math.isfinite(value) for value in (q1, q3, med, iqr)) or iqr <= 0.0:
        raise ValueError("basis distribution has no positive robust scale")
    return med, q1, q3, q1 - 1.5 * iqr, q3 + 1.5 * iqr


def rolling_atr(bars: Sequence[FiveBar], window: int) -> dict[int, float]:
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


def dislocation_direction(
    basis_bp: float,
    *,
    lower_fence: float,
    upper_fence: float,
) -> int:
    """Return +1 for premium dislocation, -1 for discount dislocation."""
    above = basis_bp > upper_fence
    below = basis_bp < lower_fence
    if above == below:
        return 0
    return 1 if above else -1


def find_compression_confirmation(
    bars: Sequence[BasisBar],
    *,
    event_index: int,
    dislocation: int,
    lower_fence: float,
    upper_fence: float,
    expiry_bars: int,
) -> tuple[int, BasisBar] | tuple[None, None]:
    if dislocation not in (-1, 1):
        raise ValueError("dislocation must be -1 or 1")
    event = bars[event_index]
    midpoint = (event.futures.high + event.futures.low) / 2.0
    last = min(len(bars), event_index + 1 + expiry_bars)
    for index in range(event_index + 1, last):
        current = bars[index]
        inside_frozen_fence = lower_fence <= current.basis_bp <= upper_fence
        reversal_direction = -dislocation
        price_reversal = (
            current.futures.close < midpoint
            if reversal_direction < 0
            else current.futures.close > midpoint
        )
        flow_reversal = reversal_direction * current.futures.flow > 0.0
        body_reversal = (
            current.futures.close < current.futures.open
            if reversal_direction < 0
            else current.futures.close > current.futures.open
        )
        if inside_frozen_fence and price_reversal and flow_reversal and body_reversal:
            return index, current
    return None, None


def derive_v23(
    *,
    prepared_root: Path,
    output_signals: Path,
    output_manifest: Path,
    basis_baseline_bars: int = 48,
    confirmation_expiry_bars: int = 3,
    atr_bars: int = 12,
    stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if basis_baseline_bars < 20:
        raise ValueError("basis baseline too short")
    if confirmation_expiry_bars <= 0 or atr_bars <= 0:
        raise ValueError("time windows must be positive")
    if stop_buffer_atr < 0.0:
        raise ValueError("stop_buffer_atr must be non-negative")

    raw_root = prepared_root / "raw"
    futures_minutes = load_kline_minutes(sorted((raw_root / "futures_kline").glob("*.zip")))
    spot_minutes = load_kline_minutes(sorted((raw_root / "spot_kline").glob("*.zip")))
    oi = load_open_interest(sorted((raw_root / "open_interest").glob("*.zip")))
    futures_map = aggregate_five(futures_minutes)
    spot_map = aggregate_five(spot_minutes)
    aligned = sorted(set(futures_map) & set(spot_map))
    basis_bars = [
        BasisBar(
            end_minute=minute,
            futures=futures_map[minute],
            spot=spot_map[minute],
            basis_bp=(futures_map[minute].close / spot_map[minute].close - 1.0) * 10_000.0,
        )
        for minute in aligned
    ]
    atr = rolling_atr([item.futures for item in basis_bars], atr_bars)
    data_manifest = json.loads((prepared_root / "data_manifest.json").read_text(encoding="utf-8"))
    evaluation_start_ns = int(data_manifest["evaluation_start_ns"])
    evaluation_end_ns = int(data_manifest["evaluation_end_ns"])

    signals: list[dict[str, Any]] = []
    state_counts: dict[str, int] = {}
    no_trade_reasons: dict[str, int] = {}
    event_counts = {"PREMIUM": 0, "DISCOUNT": 0}
    gross_rrs: list[float] = []
    cooldown_until = 0

    def count(mapping: dict[str, int], key: str) -> None:
        mapping[key] = mapping.get(key, 0) + 1

    warmup = max(basis_baseline_bars, atr_bars)
    for index in range(warmup, len(basis_bars)):
        if index < cooldown_until:
            continue
        event = basis_bars[index]
        event_end_ns = event.end_minute * NS_PER_MINUTE
        if not evaluation_start_ns <= event_end_ns < evaluation_end_ns:
            continue
        baseline_values = [item.basis_bp for item in basis_bars[index - basis_baseline_bars : index]]
        try:
            basis_median, q1, q3, lower_fence, upper_fence = tukey_fences(baseline_values)
        except ValueError:
            count(no_trade_reasons, "DEGENERATE_BASIS_BASELINE")
            continue
        dislocation = dislocation_direction(event.basis_bp, lower_fence=lower_fence, upper_fence=upper_fence)
        if dislocation == 0:
            continue
        event_counts["PREMIUM" if dislocation > 0 else "DISCOUNT"] += 1

        futures_directional_flow = dislocation * event.futures.flow
        spot_directional_flow = dislocation * event.spot.flow
        futures_return = dislocation * (event.futures.close / event.futures.open - 1.0)
        spot_return = dislocation * (event.spot.close / event.spot.open - 1.0)
        if futures_directional_flow <= 0.0:
            count(no_trade_reasons, "FUTURES_FLOW_DID_NOT_DRIVE_DISLOCATION")
            continue
        if spot_directional_flow >= futures_directional_flow:
            count(no_trade_reasons, "SPOT_FLOW_CONFIRMED_DISLOCATION")
            continue
        if futures_return <= spot_return:
            count(no_trade_reasons, "FUTURES_PRICE_DID_NOT_OUTRUN_SPOT")
            continue

        confirmation = find_compression_confirmation(
            basis_bars,
            event_index=index,
            dislocation=dislocation,
            lower_fence=lower_fence,
            upper_fence=upper_fence,
            expiry_bars=confirmation_expiry_bars,
        )
        if confirmation[0] is None:
            count(no_trade_reasons, "BASIS_COMPRESSION_CONFIRMATION_UNRESOLVED")
            continue
        confirm_index = int(confirmation[0])
        confirm_bar = confirmation[1]
        assert isinstance(confirm_bar, BasisBar)
        at = atr.get(confirm_bar.end_minute)
        if at is None or not math.isfinite(at) or at <= 0.0:
            count(no_trade_reasons, "MISSING_CAUSAL_ATR")
            continue

        direction = -dislocation
        entry_reference = confirm_bar.futures.close
        fair_value = confirm_bar.spot.close * (1.0 + basis_median / 10_000.0)
        if direction * (fair_value - entry_reference) <= 0.0:
            count(no_trade_reasons, "FAIR_VALUE_TARGET_NOT_AHEAD")
            continue
        event_extreme = event.futures.high if direction < 0 else event.futures.low
        stop = event_extreme - direction * stop_buffer_atr * at
        risk_distance = direction * (entry_reference - stop)
        reward_distance = direction * (fair_value - entry_reference)
        if risk_distance <= 0.0 or not math.isfinite(stop):
            count(no_trade_reasons, "NON_EXECUTABLE_DISLOCATION_INVALIDATION")
            continue
        gross_rr = reward_distance / risk_distance

        oi_before = oi.get(event_end_ns - 5 * NS_PER_MINUTE)
        oi_after = oi.get(confirm_bar.end_minute * NS_PER_MINUTE)
        oi_change_bp: float | None = None
        if oi_before is not None and oi_after is not None and oi_before > 0.0:
            oi_change_bp = (oi_after / oi_before - 1.0) * 10_000.0
        if oi_change_bp is None:
            state = UNKNOWN_INVENTORY_DISLOCATION_REVERSAL
        elif oi_change_bp >= 0.0:
            state = OI_EXPANSION_DISLOCATION_REVERSAL
        else:
            state = OI_CONTRACTION_DISLOCATION_REVERSAL

        confirm_ns = confirm_bar.end_minute * NS_PER_MINUTE
        suffix = sha256(
            f"{confirm_ns}|{direction}|{event.basis_bp:.12g}|{basis_median:.12g}".encode()
        ).hexdigest()[:16]
        details = {
            "scenario_kind": state,
            "entry_kind": "REVERSAL",
            "entry_sequence": [
                "CAUSAL_4H_BASIS_DISTRIBUTION",
                "TUKEY_OUTER_FENCE_DISLOCATION",
                "FUTURES_FLOW_OUTRUNS_SPOT",
                "FROZEN_FENCE_REENTRY",
                "FUTURES_FLOW_AND_PRICE_REVERSAL",
                "NEXT_NATIVE_QUOTE_ENTRY",
            ],
            "basis_baseline_bars": basis_baseline_bars,
            "basis_median_bp": basis_median,
            "basis_q1_bp": q1,
            "basis_q3_bp": q3,
            "basis_lower_fence_bp": lower_fence,
            "basis_upper_fence_bp": upper_fence,
            "event_basis_bp": event.basis_bp,
            "confirmation_basis_bp": confirm_bar.basis_bp,
            "dislocation_side": "PREMIUM" if dislocation > 0 else "DISCOUNT",
            "event_futures_flow": event.futures.flow,
            "event_spot_flow": event.spot.flow,
            "confirmation_futures_flow": confirm_bar.futures.flow,
            "event_end_time_ns": event_end_ns,
            "confirmation_end_time_ns": confirm_ns,
            "confirmation_wait_bars": confirm_index - index,
            "confirmation_spot_price": confirm_bar.spot.close,
            "static_fair_value_price": fair_value,
            "oi_change_event_to_confirmation_bp": oi_change_bp,
            "stop_anchor": event_extreme,
            "stop_buffer_atr": stop_buffer_atr,
            "gross_structural_rr_at_confirmation": gross_rr,
        }
        signal: dict[str, Any] = {
            "scenario_id": f"NT-LVCFR-V23-{state}-{suffix}",
            "scenario_kind": state,
            "entry_kind": "REVERSAL",
            "confirm_time_ns": confirm_ns,
            "eligible_time_ns": confirm_ns,
            "direction": direction,
            "initial_stop": stop,
            "atr": at,
            "first_start_time_ns": (event.end_minute - 5) * NS_PER_MINUTE,
            "first_end_time_ns": event_end_ns,
            "structural_target": fair_value,
            "target_mode": "STRUCTURAL_LIQUIDITY_OBJECTIVE",
            "disable_rapid_failure_reversal": True,
            "details": details,
        }
        signals.append(signal)
        count(state_counts, state)
        gross_rrs.append(gross_rr)
        cooldown_until = confirm_index + 1

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(json.dumps(signals, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ordered_rrs = sorted(gross_rrs)
    evaluation_days = (evaluation_end_ns - evaluation_start_ns) / (86_400 * 1_000_000_000)
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v23-basis-dislocation-compression",
        "engine_status": "independent_causal_schedule_only_no_backtest",
        "derived_signal_count": len(signals),
        "evaluation_days": evaluation_days,
        "signals_per_day": len(signals) / evaluation_days if evaluation_days > 0.0 else 0.0,
        "output_state_counts": dict(sorted(state_counts.items())),
        "event_counts": event_counts,
        "no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        "basis_baseline_bars": basis_baseline_bars,
        "confirmation_expiry_bars": confirmation_expiry_bars,
        "atr_bars": atr_bars,
        "stop_buffer_atr": stop_buffer_atr,
        "minimum_gross_structural_rr": min(gross_rrs) if gross_rrs else None,
        "median_gross_structural_rr": ordered_rrs[len(ordered_rrs) // 2] if ordered_rrs else None,
        "selection_policy": (
            "causal Tukey basis fence and frozen-fence reentry; no return-fit threshold search; "
            "NautilusTrader execution unchanged"
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
    parser.add_argument("--basis-baseline-bars", type=int, default=48)
    parser.add_argument("--confirmation-expiry-bars", type=int, default=3)
    parser.add_argument("--atr-bars", type=int, default=12)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    output = (args.output_signals or (prepared / "signals.json")).resolve()
    signals = derive_v23(
        prepared_root=prepared,
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        basis_baseline_bars=args.basis_baseline_bars,
        confirmation_expiry_bars=args.confirmation_expiry_bars,
        atr_bars=args.atr_bars,
        stop_buffer_atr=args.stop_buffer_atr,
    )
    print(json.dumps({"candidate": "candidate-03-nt-lvcfr-v23-basis-dislocation-compression", "signals": len(signals)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
