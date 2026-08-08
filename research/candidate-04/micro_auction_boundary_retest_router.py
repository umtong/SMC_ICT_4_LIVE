#!/usr/bin/env python3
"""V60 boundary-retest confirmation for failed micro auctions.

A first close back inside a completed balance proves only that an outside
auction failed locally.  V57/V59 entered immediately and paid for that first
reaction at a poor price.  V60 requires a distinct counter-auction and a first
retest of the reclaimed boundary.  Entry is emitted only when the completed
retest bar:

* touches the exact reclaimed boundary,
* closes back on the reversal side near that boundary,
* has aligned executed flow, return and futures/index basis,
* follows at least one completed counter-flow/counter-return bar,
* remains inside the original structural invalidation,
* and the reversal agrees with the completed 240-minute parent displacement.

The opposite boundary of the already completed balance is the declared causal
liquidity target.  The module emits intents only; NautilusTrader owns all
orders, fills, costs, positions, PnL and NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

import micro_auction_parent_state_router as parent


TRAPPED_REVERSAL = "MICRO_BALANCE_TRAPPED_BREAKOUT_REVERSAL"
OUTPUT_SCENARIO = "MICRO_BALANCE_BOUNDARY_RETEST_FAILED_AUCTION_REVERSAL"
PARENT_BARS = 240
RETEST_MAX_BARS = 20
BOUNDARY_CLOSE_MAX_ATR = 0.30
STOP_BUFFER_ATR = 0.10
TARGET_SOURCE = "completed_frozen_balance_opposite_boundary"


def finite(value: Any) -> float:
    return parent.finite(value)


def completed_bar_observation(open_time: Any) -> pd.Timestamp:
    return (
        pd.Timestamp(open_time)
        + pd.Timedelta(minutes=1)
        - pd.Timedelta(milliseconds=1)
    )


def parent_directional_bps(
    rich: pd.DataFrame,
    index: int,
    side: int,
) -> float:
    return parent.parent_directional_bps(rich, index, side, PARENT_BARS)


def _counterauction_exists(
    rich: pd.DataFrame,
    start: int,
    end: int,
    side: int,
) -> bool:
    if end <= start:
        return False
    segment = rich.iloc[start:end]
    if segment.empty:
        return False
    counter_return = side * segment["ret_60s_bps"].astype(float) < 0.0
    counter_flow = side * segment["flow_60s"].astype(float) < 0.0
    return bool((counter_return & counter_flow).any())


def _stop_survived(
    rich: pd.DataFrame,
    start: int,
    end: int,
    side: int,
    stop: float,
) -> bool:
    segment = rich.iloc[start : end + 1]
    if segment.empty:
        return False
    if side > 0:
        return float(segment["mark_low"].min()) > stop
    return float(segment["mark_high"].max()) < stop


def _boundary_touched_and_reclaimed(
    row: pd.Series,
    side: int,
    boundary: float,
    atr: float,
) -> bool:
    high = finite(row["mark_high"])
    low = finite(row["mark_low"])
    close = finite(row["trade_close"])
    if not all(math.isfinite(value) for value in (high, low, close, atr)):
        return False
    if atr <= 0.0:
        return False
    touched = low <= boundary if side > 0 else high >= boundary
    reclaimed = side * (close - boundary) > 0.0
    near = abs(close - boundary) <= BOUNDARY_CLOSE_MAX_ATR * atr
    return bool(touched and reclaimed and near)


def _aligned_confirmation(row: pd.Series, side: int) -> bool:
    flow = side * finite(row["flow_60s"])
    return_bps = side * finite(row["ret_60s_bps"])
    basis = side * finite(row["basis_change_5m"])
    return all(math.isfinite(value) and value > 0.0 for value in (flow, return_bps, basis))


def route_signals(
    signals: list[dict[str, Any]],
    rich: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    routed: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    scale_counts: Counter[str] = Counter()
    for raw in signals:
        counts["input_signals"] += 1
        if str(raw.get("scenario")) != TRAPPED_REVERSAL:
            counts["discarded_non_trapped_route"] += 1
            continue
        side = int(raw["side"])
        outcome = int(raw["signal_index"])
        details = dict(raw.get("details") or {})
        boundary = finite(details.get("break_boundary"))
        stop = finite(raw.get("stop_level"))
        balance_high = finite(details.get("balance_high"))
        balance_low = finite(details.get("balance_low"))
        width_atr = finite(details.get("balance_width_atr"))
        if not all(
            math.isfinite(value)
            for value in (boundary, stop, balance_high, balance_low, width_atr)
        ):
            counts["missing_structure"] += 1
            continue
        if width_atr <= 0.0 or side not in (-1, 1):
            counts["invalid_structure"] += 1
            continue
        parent_bps = parent_directional_bps(rich, outcome, side)
        if not math.isfinite(parent_bps) or parent_bps <= 0.0:
            counts["parent_misaligned"] += 1
            continue
        atr = (balance_high - balance_low) / width_atr
        target = balance_high if side > 0 else balance_low
        upper = min(outcome + RETEST_MAX_BARS, len(rich) - 2)
        selected: dict[str, Any] | None = None
        for index in range(outcome + 1, upper + 1):
            if not _stop_survived(rich, outcome + 1, index, side, stop):
                counts["structural_stop_failed_before_retest"] += 1
                break
            row = rich.iloc[index]
            if not _boundary_touched_and_reclaimed(row, side, boundary, atr):
                continue
            if not _counterauction_exists(rich, outcome + 1, index, side):
                counts["touch_without_completed_counterauction"] += 1
                continue
            if not _aligned_confirmation(row, side):
                counts["retest_without_aligned_confirmation"] += 1
                continue
            signal_time = pd.Timestamp(row["open_time"])
            observe_time = completed_bar_observation(signal_time)
            full = rich.iloc[int(details["break_index"]) : index + 1]
            if side > 0:
                retest_stop = min(stop, finite(full["mark_low"].min()) - STOP_BUFFER_ATR * atr)
            else:
                retest_stop = max(stop, finite(full["mark_high"].max()) + STOP_BUFFER_ATR * atr)
            close = finite(row["trade_close"])
            if not math.isfinite(retest_stop) or side * (close - retest_stop) <= 0.0:
                counts["invalid_retest_stop"] += 1
                continue
            enriched = {
                **details,
                "auction_outcome": "FAILED_BREAKOUT_BOUNDARY_RETEST_RECLAIMED",
                "initial_reentry_index": outcome,
                "boundary_retest_index": index,
                "boundary_retest_delay_bars": index - outcome,
                "boundary_retest_close_distance_atr": abs(close - boundary) / atr,
                "parent_directional_bars": PARENT_BARS,
                "parent_directional_bps": parent_bps,
                "causal_target_reference": target,
                "causal_target_source": TARGET_SOURCE,
                "causal_target_observed_index": int(details["balance_end_index"]),
                "v60_route": "parent_aligned_boundary_retest_failed_auction",
            }
            selected = {
                "scenario": OUTPUT_SCENARIO,
                "side": side,
                "signal_index": index,
                "signal_time": signal_time.isoformat(),
                "observe_time": observe_time.isoformat(),
                "observe_time_ns": int(observe_time.value),
                "stop_level": retest_stop,
                "event_indices": [
                    int(details["balance_start_index"]),
                    int(details["balance_end_index"]),
                    int(details["break_index"]),
                    outcome,
                    index,
                ],
                "details": enriched,
            }
            break
        if selected is None:
            counts["no_completed_boundary_retest"] += 1
            continue
        routed.append(selected)
        scale = str(details.get("micro_balance_bars", 30))
        scale_counts[scale] += 1
        counts["routed"] += 1

    routed.sort(
        key=lambda item: (
            int(item["observe_time_ns"]),
            -int(item["details"].get("micro_balance_bars", 0)),
        )
    )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for row in routed:
        key = (int(row["observe_time_ns"]), int(row["side"]))
        if key in seen:
            counts["duplicate_same_time_side"] += 1
            continue
        seen.add(key)
        unique.append(row)
    summary = {
        "candidate": "candidate-04-v60-boundary-retest-failed-auction",
        "compiler": "candidate-04-v60-boundary-retest-router-v1",
        "counts": dict(counts),
        "scale_counts": dict(scale_counts),
        "written_signals": len(unique),
        "parent_bars": PARENT_BARS,
        "retest_max_bars": RETEST_MAX_BARS,
        "boundary_close_max_atr": BOUNDARY_CLOSE_MAX_ATR,
        "target_contract": "opposite boundary of the completed frozen balance",
        "performance_calculated": False,
        "future_information_used": False,
    }
    return unique, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()
    raw = json.loads(args.signals.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError("signals must contain a JSON list")
    rich = parent.load_rich(args.rich_dir, args.symbol)
    routed, summary = route_signals(
        [dict(item) for item in raw if isinstance(item, dict)], rich
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "signals.json").write_text(
        json.dumps(routed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
