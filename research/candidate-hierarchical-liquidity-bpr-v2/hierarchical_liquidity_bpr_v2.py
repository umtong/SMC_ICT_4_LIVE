"""No-split target translation of the hierarchical BPR/IFVG setup.

The v1 target incorrectly assumed that a 15m source should always hold to a 15m-or-
larger external-liquidity objective. That is sensible for a trader who takes partials at
intervening resistance, but it is structurally wrong under this project's single-entry,
single-full-exit rule. A human trade that would de-risk at the first 5m opposing
structure cannot be translated into a full-position hold through that obstacle.

V2 therefore separates *directional source scale* from *full-exit target scale*:

* entry sources remain 15m or larger external-liquidity acquisitions;
* the route map also includes confirmed 5m external liquidity;
* the first still-unconsumed opposing level is the immutable target;
* if that first obstacle cannot provide gross RR >= 1, the trade is not available;
* the system never skips a nearer obstacle merely to manufacture a large planned RR.

All setup, timing, costs, stops, causal state and labels remain unchanged from v1.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence
import json

import pandas as pd

import hierarchical_liquidity_bpr as v1
from auction_episode_research import CONTRACTS, PivotLevel, label_action
from derivatives_dislocation import prepare_market_state
from liquidity_displacement import _add_common_state
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics

POLICY = v1.POLICY.replace(
    "SCALE_MATCHED_UNCONSUMED_EXTERNAL_LIQUIDITY",
    "FIRST_UNCONSUMED_OPPOSING_STRUCTURE_NO_SPLIT_TARGET",
)
TARGET_PIVOT_SPANS = {5: (2, 4), **v1.PIVOT_SPANS}
MINIMUM_SOURCE_TIMEFRAME = 15


def _nearest_route_target(
    levels: Sequence[v1.LiquidityLevel],
    source: v1.LiquidityLevel,
    side: str,
    emission_index: int,
    entry: float,
) -> v1.LiquidityLevel | None:
    target_side = "HIGH" if side == "LONG" else "LOW"
    candidates = [
        level
        for level in levels
        if level.side == target_side
        and level.observed_index_1m < emission_index
        and (level.first_penetration_index is None or level.first_penetration_index > emission_index)
        and ((side == "LONG" and level.price > entry) or (side == "SHORT" and level.price < entry))
    ]
    candidates.sort(
        key=lambda level: (
            abs(level.price - entry),
            -level.timeframe_minutes,
            -level.defense_count,
            -level.strength_ratio,
        )
    )
    return candidates[0] if candidates else None


def _make_action_v2(*args: Any, **kwargs: Any):
    original = v1._target_level
    v1._target_level = _nearest_route_target
    try:
        return v1._make_action(*args, **kwargs)
    finally:
        v1._target_level = original


def detect_levels_v2(symbol: str, data: pd.DataFrame, raw: pd.DataFrame, tick: float):
    original = v1.PIVOT_SPANS
    v1.PIVOT_SPANS = TARGET_PIVOT_SPANS
    try:
        return v1.detect_hierarchical_liquidity(symbol, data, raw, tick)
    finally:
        v1.PIVOT_SPANS = original


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    raw: pd.DataFrame,
    levels: Sequence[v1.LiquidityLevel],
    trading_start: date,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    tick = CONTRACTS[symbol].tick_size
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    source_levels = [level for level in levels if level.timeframe_minutes >= MINIMUM_SOURCE_TIMEFRAME]
    records: list[dict[str, Any]] = []
    first_acquisitions = 0
    setups = 0
    for source in source_levels:
        interaction = source.first_penetration_index
        if interaction is None or interaction >= len(data) or int(data.index[interaction].value) < start_ns:
            continue
        first_acquisitions += 1
        manipulation = v1._detect_manipulation(data, interaction, source)
        if manipulation is None:
            continue
        reclaim, extreme = manipulation
        setup = v1._detect_setup(data, interaction, reclaim, extreme, source, tick)
        if setup is None:
            continue
        response = v1._first_return_response(data, setup, tick)
        if response is None:
            continue
        setups += 1
        action = _make_action_v2(symbol, data, levels, source, setup, response, tick)
        if action is None:
            continue
        label = label_action(data, action, tick)
        if label.holding_minutes is not None and label.holding_minutes > v1.MAX_HOLD_MINUTES:
            continue
        records.append(
            {
                **{key: value for key, value in asdict(action).items() if key != "feature_values"},
                **action.feature_values,
                **asdict(label),
            }
        )
    raw_candidates = pd.DataFrame(records)
    if not raw_candidates.empty and raw_candidates.action_id.duplicated().any():
        raise RuntimeError(f"duplicate hierarchical v2 action id {symbol}")
    actions, suppression = v1._hierarchical_suppression(raw_candidates, data)
    if not actions.empty:
        actions["target_policy"] = "FIRST_UNCONSUMED_OPPOSING_STRUCTURE"
    return actions, {
        "symbol": symbol,
        "bars": len(data),
        "hierarchical_levels": len(levels),
        "source_levels": len(source_levels),
        "first_acquisitions": first_acquisitions,
        "complete_setups": setups,
        "raw_actions": len(raw_candidates),
        "actions": len(actions),
        "suppression": suppression,
        "outcomes": actions.outcome.value_counts().to_dict() if not actions.empty else {},
    }


def run_research(
    *,
    start: date,
    end: date,
    warmup_days: int,
    symbols: Sequence[str],
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    from data_re1_flow import load_range_flow

    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    load_start = start - timedelta(days=warmup_days)
    prepared: dict[str, pd.DataFrame] = {}
    raw_by_symbol: dict[str, pd.DataFrame] = {}
    levels_by_symbol: dict[str, list[v1.LiquidityLevel]] = {}
    for symbol in symbols:
        tick = CONTRACTS[symbol].tick_size
        raw = load_range_flow(symbol, load_start, end, cache)
        raw_by_symbol[symbol] = raw
        index_price = load_reference_range("indexPriceKlines", symbol, load_start, end, cache)
        mark_price = load_reference_range("markPriceKlines", symbol, load_start, end, cache)
        metrics = load_range_metrics(symbol, load_start, end, cache)
        data = prepare_market_state(raw, index_price, mark_price, metrics, tick)
        prepared[symbol] = data
        levels_by_symbol[symbol] = detect_levels_v2(symbol, data, raw, tick)
    prepared = _add_common_state(prepared)
    frames: list[pd.DataFrame] = []
    by_symbol: dict[str, Any] = {}
    for symbol in symbols:
        actions, summary = generate_symbol(
            symbol,
            prepared[symbol],
            raw_by_symbol[symbol],
            levels_by_symbol[symbol],
            start,
        )
        by_symbol[symbol] = summary
        if not actions.empty:
            actions.to_csv(output / f"{symbol}_hierarchical_v2_actions.csv", index=False)
            frames.append(actions)
    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    combined.to_csv(output / "hierarchical_v2_actions.csv", index=False)
    resolved = (
        combined[combined.outcome.isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_FILL_TARGET_SAME_MINUTE", "AMBIGUOUS_SAME_MINUTE"])]
        if not combined.empty else combined
    )
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "warmup_days": warmup_days,
        "symbols": list(symbols),
        "actions": len(combined),
        "resolved": len(resolved),
        "wins": int((resolved.outcome == "TARGET_FIRST").sum()) if not resolved.empty else 0,
        "win_rate": float((resolved.outcome == "TARGET_FIRST").mean()) if not resolved.empty else None,
        "by_symbol": by_symbol,
        "policy": POLICY,
        "future_information_in_features": False,
        "future_information_in_labels_only": True,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


__all__ = ["run_research", "generate_symbol", "detect_levels_v2"]
