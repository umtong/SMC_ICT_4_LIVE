#!/usr/bin/env python3
"""Harvest two independent causal alpha families into one normalized action universe.

The loader is shared, but the mechanisms remain distinct:

1. LIQUIDITY_DISPLACEMENT
   pre-existing liquidity -> failed/accepted auction -> local control break with
   fresh OB/FVG origin -> first return -> completed response.

2. DERIVATIVES_DISLOCATION
   futures/index/mark/OI dislocation -> either forced-flush repair or
   spot-confirmed initiative -> structural objective.

All decisions are emitted inside [start, end).  Data after ``end`` is loaded only
for first-passage labels.  Each action is normalized so the declared stop,
including modeled costs and slippage, is exactly -1 account R.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from auction_episode_research import (
    CONTRACTS,
    _resample_flow,
    detect_pivots,
    prepare_one_minute,
)
from data_re1_flow import load_range_flow
import derivatives_dislocation as derivatives
import liquidity_displacement as displacement
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics


LABEL_BUFFER_DAYS = 3
COMMON_HORIZONS = (15, 30, 60)
RESOLVED_OUTCOMES = {
    "TARGET_FIRST",
    "STOP_FIRST",
    "AMBIGUOUS_SAME_MINUTE",
    "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
    "AMBIGUOUS_FILL_TARGET_SAME_MINUTE",
}


def _utc_ns(value: date) -> int:
    return int(pd.Timestamp(value, tz="UTC").value)


def _finite(value: Any, default: float = float("nan")) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _reference_horizons(data: pd.DataFrame, tick: float) -> pd.DataFrame:
    """Add slower contract/index/mark returns before common-state alignment."""
    output = data.copy()
    futures = np.log(output["close"].astype(float).clip(lower=tick))
    index = np.log(output["index_close"].astype(float).clip(lower=tick))
    mark = np.log(output["mark_close"].astype(float).clip(lower=tick))
    for minutes in COMMON_HORIZONS:
        output[f"futures_return_{minutes}m"] = futures.diff(minutes)
        output[f"index_return_{minutes}m"] = index.diff(minutes)
        output[f"mark_return_{minutes}m"] = mark.diff(minutes)
    return output


def _annotate_point_in_time_state(
    actions: pd.DataFrame,
    prepared: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Join only the completed emission-bar state to each immutable action."""
    if actions.empty:
        return actions
    records: list[dict[str, float]] = []
    for row in actions.itertuples(index=False):
        symbol = str(row.symbol)
        position = int(row.emission_index)
        side_sign = 1.0 if str(row.side) == "LONG" else -1.0
        data = prepared[symbol]
        if position < 0 or position >= len(data):
            raise IndexError(f"invalid emission index {position} for {symbol}")
        state = data.iloc[position]
        values: dict[str, float] = {}
        for minutes in COMMON_HORIZONS:
            common = _finite(state.get(f"common_return_{minutes}m"), 0.0)
            residual = _finite(state.get(f"residual_return_{minutes}m"), 0.0)
            breadth = _finite(state.get(f"common_breadth_{minutes}m"), 0.0)
            values[f"common_return_{minutes}m_signed"] = side_sign * common
            values[f"residual_return_{minutes}m_signed"] = side_sign * residual
            values[f"common_breadth_{minutes}m_signed"] = side_sign * breadth
            values[f"index_return_{minutes}m_signed"] = (
                side_sign * _finite(state.get(f"index_return_{minutes}m"), 0.0)
            )
            values[f"futures_return_{minutes}m_context_signed"] = (
                side_sign * _finite(state.get(f"futures_return_{minutes}m"), 0.0)
            )
            values[f"mark_return_{minutes}m_context_signed"] = (
                side_sign * _finite(state.get(f"mark_return_{minutes}m"), 0.0)
            )
        records.append(values)
    context = pd.DataFrame(records, index=actions.index)
    for column in context:
        actions[column] = context[column]
    return actions


def _normalize_account_r(actions: pd.DataFrame) -> pd.DataFrame:
    """Size each plan from its cost-inclusive stop rather than raw price risk."""
    if actions.empty:
        return actions
    output = actions.copy()
    target = pd.to_numeric(output.get("target_net_r"), errors="coerce")
    stop = pd.to_numeric(output.get("stop_net_r"), errors="coerce")
    denominator = stop.abs().replace(0.0, np.nan)
    output["account_target_r"] = target / denominator
    output["account_stop_r"] = -1.0
    outcome = output["outcome"].astype(str)
    output["account_net_r"] = np.where(
        outcome.eq("TARGET_FIRST"),
        output["account_target_r"],
        np.where(outcome.isin(RESOLVED_OUTCOMES), -1.0, np.nan),
    )
    output["cost_inclusive_stop_normalization"] = denominator
    return output


def _filter_decision_window(
    actions: pd.DataFrame,
    start_ns: int,
    end_ns: int,
) -> pd.DataFrame:
    if actions.empty:
        return actions
    emission = pd.to_numeric(actions["emission_time_ns"], errors="coerce")
    return actions[(emission >= start_ns) & (emission < end_ns)].copy()


def run_research(
    *,
    start: date,
    end: date,
    warmup_days: int,
    symbols: Sequence[str],
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    if end <= start:
        raise ValueError("end must be after start")
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    load_start = start - timedelta(days=warmup_days)
    label_end = end + timedelta(days=LABEL_BUFFER_DAYS)
    start_ns, end_ns = _utc_ns(start), _utc_ns(end)

    displacement_state: dict[str, pd.DataFrame] = {}
    derivatives_state: dict[str, pd.DataFrame] = {}
    aggregates_by_symbol: dict[str, dict[int, pd.DataFrame]] = {}

    for symbol in symbols:
        tick = CONTRACTS[symbol].tick_size
        raw = load_range_flow(symbol, load_start, label_end, cache)
        metrics = load_range_metrics(symbol, load_start, label_end, cache)
        index_price = load_reference_range(
            "indexPriceKlines", symbol, load_start, label_end, cache
        )
        mark_price = load_reference_range(
            "markPriceKlines", symbol, load_start, label_end, cache
        )
        displacement_data = prepare_one_minute(raw, tick)
        displacement_data = displacement._merge_metrics(displacement_data, metrics)
        derivatives_data = derivatives.prepare_market_state(
            raw, index_price, mark_price, metrics, tick
        )
        derivatives_data = _reference_horizons(derivatives_data, tick)

        aggregates_by_symbol[symbol] = {
            minutes: _resample_flow(raw, minutes) for minutes in (5, 15, 60)
        }
        displacement_state[symbol] = displacement_data
        derivatives_state[symbol] = derivatives_data

    # Align first, then detect pivots so every observed_index remains valid.
    displacement_state = displacement._add_common_state(displacement_state)
    derivatives_state = displacement._add_common_state(derivatives_state)
    displacement_levels = {
        symbol: detect_pivots(
            symbol,
            displacement_state[symbol],
            aggregates_by_symbol[symbol],
            CONTRACTS[symbol].tick_size,
        )
        for symbol in symbols
    }
    derivatives_levels = {
        symbol: detect_pivots(
            symbol,
            derivatives_state[symbol],
            aggregates_by_symbol[symbol],
            CONTRACTS[symbol].tick_size,
        )
        for symbol in symbols
    }

    # Candidate existence must not depend on future holding duration.  The
    # imported research modules used label-side holding caps only to keep old
    # reports small; disable those caps before generation.
    displacement.MAX_LABEL_HOLD_MINUTES = 10**9
    derivatives.MAX_HOLD_MINUTES = 10**9

    displacement_frames: list[pd.DataFrame] = []
    derivatives_frames: list[pd.DataFrame] = []
    by_symbol: dict[str, Any] = {}

    for symbol in symbols:
        displacement_actions, displacement_summary = displacement.generate_symbol(
            symbol,
            displacement_state[symbol],
            displacement_levels[symbol],
            trading_start=start,
        )
        derivatives_actions, derivatives_summary = derivatives.generate_symbol(
            symbol,
            derivatives_state[symbol],
            derivatives_levels[symbol],
            start,
        )

        displacement_actions = _filter_decision_window(
            displacement_actions, start_ns, end_ns
        )
        derivatives_actions = _filter_decision_window(
            derivatives_actions, start_ns, end_ns
        )
        if not displacement_actions.empty:
            displacement_actions["scenario_family"] = "LIQUIDITY_DISPLACEMENT"
            displacement_actions["causal_mechanism"] = (
                displacement_actions["event_type"].astype(str)
            )
            displacement_actions = _annotate_point_in_time_state(
                displacement_actions, displacement_state
            )
            displacement_actions = _normalize_account_r(displacement_actions)
            displacement_frames.append(displacement_actions)
            displacement_actions.to_csv(
                output / f"{symbol}_liquidity_displacement.csv.gz",
                index=False,
                compression="gzip",
            )
        if not derivatives_actions.empty:
            derivatives_actions["scenario_family"] = "DERIVATIVES_DISLOCATION"
            derivatives_actions["causal_mechanism"] = (
                derivatives_actions["mechanism"].astype(str)
            )
            derivatives_actions = _annotate_point_in_time_state(
                derivatives_actions, derivatives_state
            )
            derivatives_actions = _normalize_account_r(derivatives_actions)
            derivatives_frames.append(derivatives_actions)
            derivatives_actions.to_csv(
                output / f"{symbol}_derivatives_dislocation.csv.gz",
                index=False,
                compression="gzip",
            )

        by_symbol[symbol] = {
            "liquidity_displacement": {
                **displacement_summary,
                "decision_window_actions": int(len(displacement_actions)),
            },
            "derivatives_dislocation": {
                **derivatives_summary,
                "decision_window_actions": int(len(derivatives_actions)),
            },
        }

    displacement_all = (
        pd.concat(displacement_frames, ignore_index=True, sort=False)
        if displacement_frames
        else pd.DataFrame()
    )
    derivatives_all = (
        pd.concat(derivatives_frames, ignore_index=True, sort=False)
        if derivatives_frames
        else pd.DataFrame()
    )
    combined = pd.concat(
        [displacement_all, derivatives_all],
        ignore_index=True,
        sort=False,
    )
    if not combined.empty:
        combined = combined.sort_values(
            ["emission_time_ns", "symbol", "scenario_family", "action_id"]
        ).reset_index(drop=True)
        if combined["action_id"].duplicated().any():
            duplicate = combined.loc[
                combined["action_id"].duplicated(keep=False), "action_id"
            ].iloc[0]
            raise RuntimeError(f"duplicate action id: {duplicate}")

    displacement_all.to_csv(
        output / "liquidity_displacement_actions.csv.gz",
        index=False,
        compression="gzip",
    )
    derivatives_all.to_csv(
        output / "derivatives_dislocation_actions.csv.gz",
        index=False,
        compression="gzip",
    )
    combined.to_csv(
        output / "candidate_actions.csv.gz",
        index=False,
        compression="gzip",
    )

    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "label_data_end": label_end.isoformat(),
        "warmup_days": int(warmup_days),
        "symbols": list(symbols),
        "candidate_actions": int(len(combined)),
        "by_family": {
            "LIQUIDITY_DISPLACEMENT": int(len(displacement_all)),
            "DERIVATIVES_DISLOCATION": int(len(derivatives_all)),
        },
        "by_symbol": by_symbol,
        "future_information_in_features": False,
        "future_information_in_labels_only": True,
        "decision_window_end_exclusive": True,
        "cost_inclusive_account_r_normalization": True,
        "one_action_per_family_episode": True,
        "filled_position_exit_contract": "TAKE_PROFIT_OR_STOP_LOSS_ONLY",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=75)
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
    )
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run_research(
                start=args.start,
                end=args.end,
                warmup_days=args.warmup_days,
                symbols=args.symbols,
                cache=args.cache,
                output=args.output,
            ),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
