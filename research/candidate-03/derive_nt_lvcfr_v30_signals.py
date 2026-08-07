#!/usr/bin/env python3
"""Derive V30 failed basis-reversion price-discovery continuation.

V23's fair-value compression reversals lost every executed trade across three
weeks. V30 does not invert the V23 entry. It observes the failed reversion and
requires a new causal sequence:

1. V23 basis-dislocation compression setup exists, but no order is submitted.
2. The basis re-exits the original frozen fence in the original dislocation
   direction within 30 minutes.
3. Futures and spot taker flow now agree in that original direction and futures
   closes beyond the original dislocation extreme, trapping reversion entries.
4. A later completed five-minute bar retests and defends that failed boundary.
5. Enter on the next native quote in the original price-discovery direction.

This module creates signals only. NautilusTrader remains solely responsible for
orders, fills, fees, funding, positions and NAV.
"""
from __future__ import annotations

import argparse
import json
import math
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence

from derive_nt_lvcfr_v23_signals import (
    BasisBar,
    aggregate_five,
    rolling_atr,
)
from nt_lvcfr_data import NS_PER_MINUTE, load_kline_minutes

FAILURE_EXPIRY_BARS = 6
RETEST_EXPIRY_BARS = 3
RETEST_TOUCH_BUFFER_ATR = 0.25
STOP_BUFFER_ATR = 0.20
COOLDOWN_BARS = 6
STATE = "FAILED_BASIS_REVERSION_PRICE_DISCOVERY_CONTINUATION"


def basis_series(raw_root: Path) -> tuple[list[BasisBar], dict[int, float]]:
    futures_minutes = load_kline_minutes(
        sorted((raw_root / "futures_kline").glob("*.zip"))
    )
    spot_minutes = load_kline_minutes(
        sorted((raw_root / "spot_kline").glob("*.zip"))
    )
    futures = aggregate_five(futures_minutes)
    spot = aggregate_five(spot_minutes)
    aligned = sorted(set(futures) & set(spot))
    bars = [
        BasisBar(
            end_minute=minute,
            futures=futures[minute],
            spot=spot[minute],
            basis_bp=(futures[minute].close / spot[minute].close - 1.0) * 10_000.0,
        )
        for minute in aligned
    ]
    return bars, rolling_atr([bar.futures for bar in bars], 12)


def find_failure(
    bars: Sequence[BasisBar],
    *,
    start_index: int,
    original_direction: int,
    lower_fence: float,
    upper_fence: float,
    event_extreme: float,
) -> tuple[int, BasisBar] | tuple[None, None]:
    last = min(len(bars), start_index + 1 + FAILURE_EXPIRY_BARS)
    for index in range(start_index + 1, last):
        bar = bars[index]
        outside = (
            bar.basis_bp > upper_fence
            if original_direction > 0
            else bar.basis_bp < lower_fence
        )
        beyond_extreme = (
            bar.futures.close > event_extreme
            if original_direction > 0
            else bar.futures.close < event_extreme
        )
        body = original_direction * (bar.futures.close - bar.futures.open) > 0.0
        flows = (
            original_direction * bar.futures.flow > 0.0
            and original_direction * bar.spot.flow > 0.0
        )
        if outside and beyond_extreme and body and flows:
            return index, bar
    return None, None


def find_retest(
    bars: Sequence[BasisBar],
    *,
    start_index: int,
    original_direction: int,
    event_extreme: float,
    atr: float,
) -> tuple[int, BasisBar] | tuple[None, None]:
    last = min(len(bars), start_index + 1 + RETEST_EXPIRY_BARS)
    touch_buffer = RETEST_TOUCH_BUFFER_ATR * atr
    for index in range(start_index + 1, last):
        bar = bars[index]
        if original_direction > 0:
            touched = bar.futures.low <= event_extreme + touch_buffer
            defended = bar.futures.close > event_extreme
        else:
            touched = bar.futures.high >= event_extreme - touch_buffer
            defended = bar.futures.close < event_extreme
        body = original_direction * (bar.futures.close - bar.futures.open) > 0.0
        flows = (
            original_direction * bar.futures.flow > 0.0
            and original_direction * bar.spot.flow > 0.0
        )
        if touched and defended and body and flows:
            return index, bar
    return None, None


def derive_v30(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
) -> list[dict[str, Any]]:
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    bars, atr = basis_series(raw_root)
    index_by_end = {bar.end_minute: index for index, bar in enumerate(bars)}
    signals: list[dict[str, Any]] = []
    no_trade: dict[str, int] = {}
    last_signal_index = -10**9

    def count(reason: str) -> None:
        no_trade[reason] = no_trade.get(reason, 0) + 1

    for source_signal in sorted(source, key=lambda item: int(item["confirm_time_ns"])):
        details = dict(source_signal.get("details", {}))
        confirm_minute = int(source_signal["confirm_time_ns"]) // NS_PER_MINUTE
        confirm_index = index_by_end.get(confirm_minute)
        if confirm_index is None:
            count("SOURCE_CONFIRMATION_NOT_ALIGNED")
            continue
        dislocation = 1 if details.get("dislocation_side") == "PREMIUM" else -1
        lower_fence = float(details["basis_lower_fence_bp"])
        upper_fence = float(details["basis_upper_fence_bp"])
        event_extreme = float(details["stop_anchor"])
        failure_index, failure_bar = find_failure(
            bars,
            start_index=confirm_index,
            original_direction=dislocation,
            lower_fence=lower_fence,
            upper_fence=upper_fence,
            event_extreme=event_extreme,
        )
        if failure_index is None or failure_bar is None:
            count("BASIS_REVERSION_DID_NOT_FAIL")
            continue
        at = atr.get(failure_bar.end_minute)
        if at is None or not math.isfinite(at) or at <= 0.0:
            count("MISSING_CAUSAL_ATR")
            continue
        retest_index, retest_bar = find_retest(
            bars,
            start_index=failure_index,
            original_direction=dislocation,
            event_extreme=event_extreme,
            atr=at,
        )
        if retest_index is None or retest_bar is None:
            count("FAILED_REVERSION_RETEST_UNRESOLVED")
            continue
        if retest_index - last_signal_index < COOLDOWN_BARS:
            count("INDEPENDENT_EVENT_COOLDOWN")
            continue
        stop = (
            retest_bar.futures.low - STOP_BUFFER_ATR * at
            if dislocation > 0
            else retest_bar.futures.high + STOP_BUFFER_ATR * at
        )
        entry_reference = retest_bar.futures.close
        if dislocation * (entry_reference - stop) <= 0.0:
            count("INVALID_LOCAL_STOP")
            continue
        confirm_ns = retest_bar.end_minute * NS_PER_MINUTE
        suffix = sha256(
            f"{confirm_ns}|{dislocation}|{event_extreme:.12g}|{stop:.12g}".encode()
        ).hexdigest()[:16]
        signal = {
            "scenario_id": f"NT-LVCFR-V30-{STATE}-{suffix}",
            "scenario_kind": STATE,
            "entry_kind": "CONTINUATION",
            "confirm_time_ns": confirm_ns,
            "eligible_time_ns": confirm_ns,
            "direction": dislocation,
            "initial_stop": stop,
            "atr": at,
            "first_start_time_ns": int(source_signal["first_start_time_ns"]),
            "first_end_time_ns": int(source_signal["first_end_time_ns"]),
            "target_mode": "EXISTING_NET_R_OBJECTIVE",
            "disable_rapid_failure_reversal": True,
            "details": {
                "scenario_kind": STATE,
                "entry_kind": "CONTINUATION",
                "source_v23_scenario_id": source_signal["scenario_id"],
                "source_reversion_direction": int(source_signal["direction"]),
                "original_dislocation_direction": dislocation,
                "event_extreme": event_extreme,
                "frozen_lower_fence_bp": lower_fence,
                "frozen_upper_fence_bp": upper_fence,
                "failure_end_minute": failure_bar.end_minute,
                "failure_basis_bp": failure_bar.basis_bp,
                "failure_futures_flow": failure_bar.futures.flow,
                "failure_spot_flow": failure_bar.spot.flow,
                "retest_end_minute": retest_bar.end_minute,
                "retest_basis_bp": retest_bar.basis_bp,
                "retest_futures_flow": retest_bar.futures.flow,
                "retest_spot_flow": retest_bar.spot.flow,
                "retest_touch_buffer_atr": RETEST_TOUCH_BUFFER_ATR,
                "stop_buffer_atr": STOP_BUFFER_ATR,
            },
        }
        signals.append(signal)
        last_signal_index = retest_index

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v30-failed-basis-reversion-continuation",
        "engine_status": "causal_failed_basis_reversion_schedule_only_no_backtest",
        "source_signal_count": len(source),
        "derived_signal_count": len(signals),
        "signals_per_day": len(signals) / 7.0,
        "state_counts": {STATE: len(signals)},
        "no_trade_reasons": dict(sorted(no_trade.items())),
        "failure_expiry_bars": FAILURE_EXPIRY_BARS,
        "retest_expiry_bars": RETEST_EXPIRY_BARS,
        "retest_touch_buffer_atr": RETEST_TOUCH_BUFFER_ATR,
        "stop_buffer_atr": STOP_BUFFER_ATR,
        "selection_policy": (
            "V23 fair-value reversion setup is never entered; same frozen basis "
            "fence re-break, original extreme break, futures/spot flow agreement "
            "and completed failed-boundary retest are required"
        ),
    }
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return signals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-signals", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output-signals", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    signals = derive_v30(
        source_signals=args.source_signals.resolve(),
        raw_root=args.raw_root.resolve(),
        output_signals=args.output_signals.resolve(),
        output_manifest=args.output_manifest.resolve(),
    )
    print(json.dumps({"candidate": "V30", "signals": len(signals)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
