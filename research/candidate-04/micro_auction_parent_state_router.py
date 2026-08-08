#!/usr/bin/env python3
"""V58 parent-state routing for frequent completed micro-auction failures.

The V57b first BTC week established two distinct facts:

* liquidation OI contraction followed by one re-entry is not exhaustion; fading
  it lost twice;
* new-inventory breakouts which failed back into the balance were directionally
  useful when the reversal resumed the already completed four-hour parent move.

V58 therefore keeps only parent-aligned trapped-inventory reversals and uses the
opposite boundary of the already completed frozen balance as their semantic
liquidity destination.  A liquidation event is not faded.  It can become a
continuation only after the failed reversal itself fails: price must close back
outside the original boundary with aligned flow, return and basis, and that
second displacement must be no larger than the original liquidation impulse.

The module emits intents only.  NautilusTrader owns orders, fills, costs,
positions, PnL and NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


TRAPPED_REVERSAL = "MICRO_BALANCE_TRAPPED_BREAKOUT_REVERSAL"
LIQUIDATION_REVERSAL = "MICRO_BALANCE_LIQUIDATION_EXHAUSTION_REVERSAL"
LIQUIDATION_REENTRY_FAILURE = (
    "MICRO_BALANCE_LIQUIDATION_REENTRY_FAILURE_CONTINUATION"
)
PARENT_BARS = 240
REENTRY_FAILURE_BARS = 15
NON_CLIMACTIC_RATIO = 1.0
STOP_BUFFER_ATR = 0.10
TARGET_SOURCE = "completed_frozen_balance_opposite_boundary"


def finite(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def load_rich(directory: Path, symbol: str = "BTCUSDT") -> pd.DataFrame:
    files = sorted(directory.glob(f"{symbol}-rich-*.csv.gz"))
    if not files:
        raise RuntimeError(f"no {symbol} rich features in {directory}")
    frame = pd.concat((pd.read_csv(path) for path in files), ignore_index=True)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame["observed_time"] = pd.to_datetime(frame["observed_time"], utc=True)
    frame = frame.sort_values("open_time").drop_duplicates("open_time")
    frame = frame.reset_index(drop=True)
    expected = frame["open_time"] + pd.Timedelta(minutes=1)
    if not (frame["observed_time"].array == expected.array).all():
        raise RuntimeError("rich features violate the close-observed contract")
    return frame


def parent_directional_bps(
    rich: pd.DataFrame,
    index: int,
    side: int,
    bars: int = PARENT_BARS,
) -> float:
    if side not in (-1, 1) or index < bars or index >= len(rich):
        return float("nan")
    current = finite(rich["trade_close"].iloc[index])
    prior = finite(rich["trade_close"].iloc[index - bars])
    if not math.isfinite(current) or not math.isfinite(prior) or prior <= 0.0:
        return float("nan")
    return side * (current / prior - 1.0) * 10_000.0


def _with_balance_target(signal: dict[str, Any]) -> dict[str, Any]:
    side = int(signal["side"])
    details = dict(signal.get("details") or {})
    target = finite(details.get("balance_high" if side > 0 else "balance_low"))
    observed = int(details["balance_end_index"])
    if not math.isfinite(target):
        raise RuntimeError("trapped reversal has no completed balance target")
    routed = dict(signal)
    details.update(
        {
            "causal_target_reference": target,
            "causal_target_source": TARGET_SOURCE,
            "causal_target_observed_index": observed,
            "parent_directional_bars": PARENT_BARS,
            "v58_route": "parent_aligned_trapped_inventory_to_opposite_balance_boundary",
        }
    )
    routed["details"] = details
    return routed


def _liquidation_reentry_failure(
    signal: dict[str, Any],
    rich: pd.DataFrame,
) -> dict[str, Any] | None:
    details = dict(signal.get("details") or {})
    outcome_index = int(details["outcome_index"])
    break_index = int(details["break_index"])
    break_side = int(details["break_side"])
    boundary = finite(details["break_boundary"])
    balance_high = finite(details["balance_high"])
    balance_low = finite(details["balance_low"])
    width_atr = finite(details["balance_width_atr"])
    if not all(
        math.isfinite(value)
        for value in (boundary, balance_high, balance_low, width_atr)
    ):
        return None
    if width_atr <= 0.0 or break_side not in (-1, 1):
        return None
    parent_bps = parent_directional_bps(rich, outcome_index, break_side)
    if not math.isfinite(parent_bps) or parent_bps <= 0.0:
        return None
    original_return = break_side * finite(rich["ret_60s_bps"].iloc[break_index])
    if not math.isfinite(original_return) or original_return <= 0.0:
        return None

    upper = min(outcome_index + REENTRY_FAILURE_BARS, len(rich) - 2)
    for index in range(outcome_index + 1, upper + 1):
        row = rich.iloc[index]
        close = finite(row["trade_close"])
        if not math.isfinite(close) or break_side * (close - boundary) <= 0.0:
            continue
        flow = break_side * finite(row["flow_60s"])
        return_bps = break_side * finite(row["ret_60s_bps"])
        basis = break_side * finite(row["basis_change_5m"])
        if not all(math.isfinite(value) and value > 0.0 for value in (flow, return_bps, basis)):
            continue
        if return_bps > NON_CLIMACTIC_RATIO * original_return:
            continue

        atr = (balance_high - balance_low) / width_atr
        segment = rich.iloc[outcome_index : index + 1]
        if segment.empty or not math.isfinite(atr) or atr <= 0.0:
            return None
        if break_side > 0:
            stop = finite(segment["mark_low"].min()) - STOP_BUFFER_ATR * atr
        else:
            stop = finite(segment["mark_high"].max()) + STOP_BUFFER_ATR * atr
        if not math.isfinite(stop) or break_side * (close - stop) <= 0.0:
            return None

        observed_time = pd.Timestamp(row["observed_time"])
        signal_time = pd.Timestamp(row["open_time"])
        continuation_details = {
            **details,
            "auction_outcome": "LIQUIDATION_REENTRY_REVERSAL_FAILED_NON_CLIMACTICALLY",
            "original_reentry_outcome_index": outcome_index,
            "outcome_index": index,
            "liquidation_reentry_failure_index": index,
            "liquidation_reentry_failure_directional_flow_60s": flow,
            "liquidation_reentry_failure_directional_return_60s_bps": return_bps,
            "liquidation_reentry_failure_directional_basis_change_5m_bps": basis,
            "original_liquidation_break_directional_return_60s_bps": original_return,
            "non_climactic_ratio": return_bps / original_return,
            "parent_directional_bars": PARENT_BARS,
            "parent_directional_bps": parent_bps,
            "v58_route": "parent_aligned_liquidation_reentry_failure_continuation",
        }
        return {
            "scenario": LIQUIDATION_REENTRY_FAILURE,
            "side": break_side,
            "signal_index": index,
            "signal_time": signal_time.isoformat(),
            "observe_time": observed_time.isoformat(),
            "observe_time_ns": int(observed_time.value),
            "stop_level": stop,
            "event_indices": [
                int(details["balance_start_index"]),
                int(details["balance_end_index"]),
                break_index,
                outcome_index,
                index,
            ],
            "details": continuation_details,
        }
    return None


def route_signals(
    signals: list[dict[str, Any]],
    rich: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    routed: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    scenario_counts: Counter[str] = Counter()
    for signal in signals:
        counts["input_signals"] += 1
        scenario = str(signal.get("scenario"))
        index = int(signal["signal_index"])
        side = int(signal["side"])
        selected: dict[str, Any] | None = None
        if scenario == TRAPPED_REVERSAL:
            parent_bps = parent_directional_bps(rich, index, side)
            if not math.isfinite(parent_bps) or parent_bps <= 0.0:
                counts["trapped_reversal_parent_misaligned"] += 1
                continue
            selected = _with_balance_target(signal)
            selected["details"]["parent_directional_bps"] = parent_bps
            counts["parent_aligned_trapped_reversal"] += 1
        elif scenario == LIQUIDATION_REVERSAL:
            selected = _liquidation_reentry_failure(signal, rich)
            if selected is None:
                counts["liquidation_reentry_failure_not_confirmed"] += 1
                continue
            counts["non_climactic_liquidation_reentry_failure"] += 1
        else:
            counts["discarded_other_scenario"] += 1
            continue
        routed.append(selected)
        scenario_counts[str(selected["scenario"])] += 1

    routed.sort(key=lambda item: int(item["observe_time_ns"]))
    unique: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for signal in routed:
        key = (int(signal["observe_time_ns"]), int(signal["side"]))
        if key in seen:
            counts["duplicate_intent"] += 1
            continue
        seen.add(key)
        unique.append(signal)
    summary = {
        "candidate": "candidate-04-v58-parent-state-micro-auction",
        "compiler": "candidate-04-v58-parent-state-router-v1",
        "counts": dict(counts),
        "scenario_counts": dict(scenario_counts),
        "written_signals": len(unique),
        "parent_directional_bars": PARENT_BARS,
        "liquidation_reentry_failure_bars": REENTRY_FAILURE_BARS,
        "non_climactic_ratio": NON_CLIMACTIC_RATIO,
        "target_contract": "completed frozen opposite balance boundary for trapped reversals; execution registry for continuations",
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
    rich = load_rich(args.rich_dir, args.symbol)
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
