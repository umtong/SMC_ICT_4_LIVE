#!/usr/bin/env python3
"""Causal liquidity-transfer narrative study.

This is a structural reset, not another threshold layer on the prior candidate.
A trade exists only after a complete auction narrative has occurred:

1. a visible multi-touch 15m/60m liquidity pool matures;
2. its first later interaction either sweeps and reclaims it or is accepted;
3. completed price/flow creates genuine displacement and a fresh 1m FVG;
4. price detaches and returns to that footprint for the first mitigation;
5. entry, sweep-origin invalidation, and the nearest still-live opposing pool
   are frozen before the next-open market entry.

Open-interest and positioning metrics are joined backward/as-of and are features,
not hindsight gates. Future barriers are added only after the plan is frozen.
One pool interaction owns one causal event and can produce at most one plan.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from auction_transition_study import (
    SYMBOLS,
    TICKS,
    Pivot,
    add_cross_features,
    aggregate,
    confirmed_pivots,
    economics,
    make_features,
    nearest_opposing_target,
    snapshot_features,
)
from data_derivatives import join_metrics_causally, load_metrics_range
from data_re1_flow import load_range_flow

R_TARGETS = (0.75, 1.0, 1.25, 1.5, 2.0)
MAX_HOLD_MINUTES = 480


@dataclass(frozen=True)
class LiquidityPool:
    pool_id: str
    timeframe: int
    span: int
    side: str
    first_pivot_id: str
    opposite_pivot_id: str
    second_pivot_id: str
    first_price: float
    second_price: float
    outer: float
    inner: float
    observed_ts: pd.Timestamp
    first_event_ts: pd.Timestamp
    second_event_ts: pd.Timestamp
    separation_bars: int
    equality_atr: float
    strength: float
    atr: float
    first_interaction_ts: pd.Timestamp | None


@dataclass(frozen=True)
class StateEvent:
    state: str
    side: int
    interaction_ts: pd.Timestamp
    confirm_ts: pd.Timestamp
    sweep_extreme: float
    sequence_low: float
    sequence_high: float
    interaction_index: int
    confirm_index: int


@dataclass(frozen=True)
class FVG:
    completion_index: int
    completion_ts: pd.Timestamp
    lower: float
    upper: float
    middle_ts: pd.Timestamp
    middle_range_ratio: float
    middle_body_aligned: float
    gap_bps: float
    impulse_progress_sigma: float


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _true_range(bars: pd.DataFrame) -> pd.Series:
    previous = bars["close"].shift(1)
    return pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous).abs(),
            (bars["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _opposite_between(pivots: list[Pivot], first: Pivot, second: Pivot) -> Pivot | None:
    wanted = "HIGH" if first.side == "LOW" else "LOW"
    choices = [
        pivot
        for pivot in pivots
        if pivot.timeframe == first.timeframe
        and pivot.side == wanted
        and first.event_ts < pivot.event_ts < second.event_ts
        and pivot.observed_ts <= second.observed_ts
    ]
    return max(
        choices,
        key=lambda pivot: (pivot.span, pivot.strength, pivot.event_ts, pivot.pivot_id),
        default=None,
    )


def _first_interaction(
    bars5: pd.DataFrame,
    observed_ts: pd.Timestamp,
    side: str,
    outer: float,
) -> pd.Timestamp | None:
    later = bars5.loc[bars5.index > observed_ts]
    if later.empty:
        return None
    touched = later["low"].le(outer) if side == "LOW" else later["high"].ge(outer)
    hits = np.flatnonzero(touched.to_numpy(bool))
    return None if not len(hits) else later.index[int(hits[0])]


def build_liquidity_pools(
    frame: pd.DataFrame,
    timeframe: int,
    spans: Iterable[int] = (2, 4),
) -> tuple[list[LiquidityPool], list[Pivot]]:
    bars = aggregate(frame, timeframe)
    tr = _true_range(bars)
    atr = tr.rolling(24, min_periods=8).median().shift(1)
    pivots = confirmed_pivots(frame, timeframe, spans)
    bars5 = aggregate(frame, 5)
    pools: list[LiquidityPool] = []
    used_second: set[str] = set()
    for second in sorted(pivots, key=lambda p: (p.observed_ts, p.event_ts, p.pivot_id)):
        if second.pivot_id in used_second:
            continue
        local_atr = _finite(atr.reindex([second.event_ts], method="ffill").iloc[0])
        if local_atr is None or local_atr <= 0.0:
            continue
        candidates: list[tuple[Pivot, Pivot, float]] = []
        for first in pivots:
            if (
                first.pivot_id == second.pivot_id
                or first.side != second.side
                or first.span != second.span
                or first.event_ts >= second.event_ts
            ):
                continue
            separation = int((second.event_ts - first.event_ts) / pd.Timedelta(minutes=timeframe))
            if separation < 4 or separation > 128:
                continue
            equality = abs(second.price - first.price) / local_atr
            if equality > 0.40:
                continue
            opposite = _opposite_between(pivots, first, second)
            if opposite is None:
                continue
            candidates.append((first, opposite, equality))
        if not candidates:
            continue
        first, opposite, equality = max(
            candidates,
            key=lambda item: (item[0].event_ts, item[0].strength, item[0].pivot_id),
        )
        outer = min(first.price, second.price) if second.side == "LOW" else max(first.price, second.price)
        inner = max(first.price, second.price) if second.side == "LOW" else min(first.price, second.price)
        observed = max(first.observed_ts, opposite.observed_ts, second.observed_ts)
        interaction = _first_interaction(bars5, observed, second.side, outer)
        separation = int((second.event_ts - first.event_ts) / pd.Timedelta(minutes=timeframe))
        pool = LiquidityPool(
            pool_id=(
                f"{timeframe}m:{second.side}:s{second.span}:"
                f"{first.pivot_id}|{opposite.pivot_id}|{second.pivot_id}"
            ),
            timeframe=timeframe,
            span=second.span,
            side=second.side,
            first_pivot_id=first.pivot_id,
            opposite_pivot_id=opposite.pivot_id,
            second_pivot_id=second.pivot_id,
            first_price=first.price,
            second_price=second.price,
            outer=outer,
            inner=inner,
            observed_ts=observed,
            first_event_ts=first.event_ts,
            second_event_ts=second.event_ts,
            separation_bars=separation,
            equality_atr=equality,
            strength=min(first.strength, second.strength),
            atr=local_atr,
            first_interaction_ts=interaction,
        )
        used_second.add(second.pivot_id)
        pools.append(pool)
    return pools, pivots


def classify_first_interaction(pool: LiquidityPool, bars5: pd.DataFrame, tick: float) -> StateEvent | None:
    ts = pool.first_interaction_ts
    if ts is None or ts not in bars5.index:
        return None
    location = bars5.index.get_loc(ts)
    if isinstance(location, slice) or location >= len(bars5) - 2:
        return None
    i = int(location)
    sequence = bars5.iloc[i : min(i + 4, len(bars5))]
    if sequence.empty:
        return None
    if pool.side == "LOW":
        if float(sequence["low"].iloc[0]) >= pool.outer - tick * 0.5:
            return None
        sweep_extreme = float(sequence["low"].min())
        for offset, (_, bar) in enumerate(sequence.iloc[:3].iterrows()):
            if float(bar.close) > pool.inner:
                return StateEvent(
                    state="SWEEP_RECLAIM",
                    side=1,
                    interaction_ts=ts,
                    confirm_ts=sequence.index[offset],
                    sweep_extreme=sweep_extreme,
                    sequence_low=float(sequence.iloc[: offset + 1]["low"].min()),
                    sequence_high=float(sequence.iloc[: offset + 1]["high"].max()),
                    interaction_index=i,
                    confirm_index=i + offset,
                )
        first = sequence.iloc[0]
        second = sequence.iloc[1]
        if float(first.close) < pool.outer and float(second.open) < pool.inner and float(second.close) < pool.outer:
            return StateEvent(
                state="ACCEPTED_BREAK",
                side=-1,
                interaction_ts=ts,
                confirm_ts=sequence.index[1],
                sweep_extreme=sweep_extreme,
                sequence_low=float(sequence.iloc[:2]["low"].min()),
                sequence_high=float(sequence.iloc[:2]["high"].max()),
                interaction_index=i,
                confirm_index=i + 1,
            )
        return None
    if float(sequence["high"].iloc[0]) <= pool.outer + tick * 0.5:
        return None
    sweep_extreme = float(sequence["high"].max())
    for offset, (_, bar) in enumerate(sequence.iloc[:3].iterrows()):
        if float(bar.close) < pool.inner:
            return StateEvent(
                state="SWEEP_RECLAIM",
                side=-1,
                interaction_ts=ts,
                confirm_ts=sequence.index[offset],
                sweep_extreme=sweep_extreme,
                sequence_low=float(sequence.iloc[: offset + 1]["low"].min()),
                sequence_high=float(sequence.iloc[: offset + 1]["high"].max()),
                interaction_index=i,
                confirm_index=i + offset,
            )
    first = sequence.iloc[0]
    second = sequence.iloc[1]
    if float(first.close) > pool.outer and float(second.open) > pool.inner and float(second.close) > pool.outer:
        return StateEvent(
            state="ACCEPTED_BREAK",
            side=1,
            interaction_ts=ts,
            confirm_ts=sequence.index[1],
            sweep_extreme=sweep_extreme,
            sequence_low=float(sequence.iloc[:2]["low"].min()),
            sequence_high=float(sequence.iloc[:2]["high"].max()),
            interaction_index=i,
            confirm_index=i + 1,
        )
    return None


def find_displacement_fvg(
    frame: pd.DataFrame,
    side: int,
    confirm_ts: pd.Timestamp,
    tick: float,
) -> FVG | None:
    index = frame.index
    start = int(index.searchsorted(confirm_ts + pd.Timedelta(minutes=1), side="left"))
    end = min(start + 31, len(frame))
    if start < 2:
        start = 2
    reference_close = float(frame.loc[:confirm_ts, "close"].iloc[-1])
    sigma = max(float(frame.loc[:confirm_ts, "prior_sigma"].iloc[-1]), 1e-12)
    for i in range(start, end):
        first = frame.iloc[i - 2]
        middle = frame.iloc[i - 1]
        third = frame.iloc[i]
        middle_range = max(float(middle.high - middle.low), tick)
        body_aligned = side * float(middle.close - middle.open) / middle_range
        range_ratio = float(middle.range_ratio)
        if not math.isfinite(range_ratio) or range_ratio < 1.25 or body_aligned < 0.45:
            continue
        if side > 0:
            lower = float(first.high)
            upper = float(third.low)
            gap = upper - lower
            progressed = float(third.close) > reference_close
        else:
            lower = float(third.high)
            upper = float(first.low)
            gap = upper - lower
            progressed = float(third.close) < reference_close
        if gap < tick or not progressed:
            continue
        progress_sigma = side * math.log(max(float(third.close), 1e-12) / max(reference_close, 1e-12)) / sigma
        if progress_sigma < 1.0:
            continue
        return FVG(
            completion_index=i,
            completion_ts=index[i],
            lower=lower,
            upper=upper,
            middle_ts=index[i - 1],
            middle_range_ratio=range_ratio,
            middle_body_aligned=body_aligned,
            gap_bps=gap / float(third.close) * 1e4,
            impulse_progress_sigma=progress_sigma,
        )
    return None


def find_first_mitigation(
    frame: pd.DataFrame,
    side: int,
    fvg: FVG,
    hard_invalidation: float,
    tick: float,
) -> int | None:
    start = fvg.completion_index + 1
    end = min(start + 121, len(frame) - 1)
    detached = False
    zone_width = max(fvg.upper - fvg.lower, tick)
    midpoint = 0.5 * (fvg.lower + fvg.upper)
    for i in range(start, end):
        bar = frame.iloc[i]
        if side > 0 and float(bar.low) <= hard_invalidation:
            return None
        if side < 0 and float(bar.high) >= hard_invalidation:
            return None
        if not detached:
            detached = (
                float(bar.close) >= fvg.upper + 0.25 * zone_width
                if side > 0
                else float(bar.close) <= fvg.lower - 0.25 * zone_width
            )
            continue
        touched = float(bar.low) <= fvg.upper if side > 0 else float(bar.high) >= fvg.lower
        held = float(bar.close) >= midpoint if side > 0 else float(bar.close) <= midpoint
        if touched and held:
            return i
    return None


def _active_target_pool(
    pools: list[LiquidityPool],
    side: int,
    entry: float,
    decision_ts: pd.Timestamp,
    source_pool_id: str,
) -> tuple[LiquidityPool, float] | None:
    wanted = "HIGH" if side > 0 else "LOW"
    choices: list[tuple[LiquidityPool, float]] = []
    for pool in pools:
        if pool.pool_id == source_pool_id or pool.side != wanted or pool.observed_ts >= decision_ts:
            continue
        if pool.first_interaction_ts is not None and pool.first_interaction_ts <= decision_ts:
            continue
        target = pool.inner
        if (side > 0 and target > entry) or (side < 0 and target < entry):
            choices.append((pool, target))
    if not choices:
        return None
    if side > 0:
        return min(choices, key=lambda item: (item[1], -item[0].timeframe, item[0].pool_id))
    return max(choices, key=lambda item: (item[1], item[0].timeframe, item[0].pool_id))


def _metric_snapshot(frame: pd.DataFrame, ts: pd.Timestamp, prefix: str) -> dict[str, float]:
    before = frame.loc[frame.index <= ts]
    if before.empty:
        return {}
    row = before.iloc[-1]
    keys = (
        "metric_age_minutes",
        "oi_value_change_5",
        "oi_value_change_15",
        "oi_value_change_30",
        "oi_value_change_60",
        "oi_value_change_180",
        "oi_change_15_z",
        "oi_change_60_z",
        "metric_taker_imbalance",
        "metric_taker_imbalance_z",
        "global_account_imbalance",
        "global_account_imbalance_z",
        "top_account_imbalance",
        "top_account_imbalance_z",
        "top_position_imbalance",
        "top_position_imbalance_z",
    )
    result: dict[str, float] = {}
    for key in keys:
        value = _finite(row.get(key))
        if value is not None:
            result[f"{prefix}_{key}"] = value
    return result


def _metric_change(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    left = frame.loc[frame.index <= start]
    right = frame.loc[frame.index <= end]
    if left.empty or right.empty:
        return None
    a = _finite(left.iloc[-1].get("oi_value_log"))
    b = _finite(right.iloc[-1].get("oi_value_log"))
    if a is None or b is None:
        return None
    return b - a


def _pre_context(frame: pd.DataFrame, bars5: pd.DataFrame, ts: pd.Timestamp, side: int) -> dict[str, float]:
    out: dict[str, float] = {}
    before = bars5.loc[bars5.index < ts]
    if len(before) >= 42:
        recent = (before.high - before.low).tail(6)
        prior = (before.high - before.low).iloc[-42:-6]
        out["pre_contraction_ratio"] = float(recent.median() / max(float(prior.median()), 1e-12))
        out["pre_range_ratio"] = float(recent.mean() / max(float(prior.mean()), 1e-12))
        out["pre_location_36"] = float(
            (before.close.iloc[-1] - before.low.iloc[-36:].min())
            / max(float(before.high.iloc[-36:].max() - before.low.iloc[-36:].min()), 1e-12)
        )
    minute = frame.loc[frame.index < ts].tail(60)
    if len(minute) >= 15:
        returns = np.log(minute.close.astype(float).clip(lower=1e-12)).diff().dropna()
        out["pre_path_eff_60"] = float(
            side * math.log(float(minute.close.iloc[-1]) / float(minute.open.iloc[0]))
            / max(float(returns.abs().sum()), 1e-12)
        )
    return out


def _event_flow_features(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    side: int,
) -> dict[str, float]:
    window = frame.loc[(frame.index >= start) & (frame.index <= end)]
    if window.empty:
        return {}
    quote = window.quote_volume.astype(float).clip(lower=0.0)
    signed = 2.0 * window.taker_buy_quote_volume.astype(float) - quote
    returns = np.log(window.close.astype(float).clip(lower=1e-12)).diff().fillna(0.0)
    net = side * math.log(float(window.close.iloc[-1]) / float(window.open.iloc[0]))
    total = float(returns.abs().sum())
    aligned_delta = side * float(signed.sum()) / max(float(quote.sum()), 1e-12)
    return {
        "event_minutes": float(len(window)),
        "event_price_progress": net,
        "event_path_efficiency": net / max(total, 1e-12),
        "event_delta_aligned": aligned_delta,
        "event_adverse_absorption": max(-aligned_delta, 0.0) * max(net, 0.0),
        "event_activity_mean": float(window.activity_ratio.mean()),
        "event_activity_max": float(window.activity_ratio.max()),
        "event_range_ratio_mean": float(window.range_ratio.mean()),
    }


def _barrier_label(
    frame: pd.DataFrame,
    side: int,
    entry_i: int,
    entry: float,
    stop: float,
    target: float,
    tick: float,
) -> dict[str, Any]:
    econ = economics(side, entry, stop, target, tick)
    future = frame.iloc[entry_i : min(entry_i + MAX_HOLD_MINUTES, len(frame))]
    outcome = "UNRESOLVED"
    resolution: pd.Timestamp | None = None
    for ts, bar in future.iterrows():
        stop_hit = float(bar.low) <= stop if side > 0 else float(bar.high) >= stop
        target_hit = float(bar.high) >= target if side > 0 else float(bar.low) <= target
        if stop_hit:
            outcome = "AMBIGUOUS_SAME_MINUTE" if target_hit else "STOP_FIRST"
            resolution = ts
            break
        if target_hit:
            outcome = "TARGET_FIRST"
            resolution = ts
            break
    label = 1.0 if outcome == "TARGET_FIRST" else 0.0 if outcome in {"STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"} else np.nan
    net_r = econ["fixed_risk_win_r"] if label == 1.0 else -1.0 if label == 0.0 else np.nan
    return {
        **econ,
        "outcome": outcome,
        "label": label,
        "net_r": net_r,
        "resolution_ts": None if resolution is None else resolution.isoformat(),
        "minutes_to_resolution": None if resolution is None else int((resolution - frame.index[entry_i]) / pd.Timedelta(minutes=1)),
    }


def _path_labels(frame: pd.DataFrame, side: int, entry_i: int, entry: float, risk: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for horizon in (5, 15, 30, 60, 120, 240, 480):
        future = frame.iloc[entry_i : min(entry_i + horizon, len(frame))]
        if future.empty:
            continue
        if side > 0:
            out[f"mfe_r_{horizon}"] = (float(future.high.max()) - entry) / risk
            out[f"mae_r_{horizon}"] = (entry - float(future.low.min())) / risk
        else:
            out[f"mfe_r_{horizon}"] = (entry - float(future.low.min())) / risk
            out[f"mae_r_{horizon}"] = (float(future.high.max()) - entry) / risk
    return out


def build_plan(
    symbol: str,
    frame: pd.DataFrame,
    bars5: pd.DataFrame,
    pools: list[LiquidityPool],
    pivots: list[Pivot],
    pool: LiquidityPool,
    event: StateEvent,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
) -> dict[str, Any] | None:
    if event.interaction_ts < start_ts or event.interaction_ts > end_ts:
        return None
    tick = TICKS[symbol]
    buffer = max(2.0 * tick, 0.04 * pool.atr)
    if event.state == "SWEEP_RECLAIM":
        hard_invalidation = event.sweep_extreme - buffer if event.side > 0 else event.sweep_extreme + buffer
    else:
        hard_invalidation = pool.inner - buffer if event.side > 0 else pool.inner + buffer
    fvg = find_displacement_fvg(frame, event.side, event.confirm_ts, tick)
    if fvg is None:
        return None
    mitigation_i = find_first_mitigation(frame, event.side, fvg, hard_invalidation, tick)
    if mitigation_i is None or mitigation_i >= len(frame) - 1:
        return None
    decision_ts = frame.index[mitigation_i]
    entry_i = mitigation_i + 1
    entry_ts = frame.index[entry_i]
    entry = float(frame.iloc[entry_i].open)
    mitigation = frame.iloc[mitigation_i]
    if event.state == "SWEEP_RECLAIM":
        stop = event.sweep_extreme - buffer if event.side > 0 else event.sweep_extreme + buffer
    else:
        stop = (
            min(float(mitigation.low), pool.inner) - buffer
            if event.side > 0
            else max(float(mitigation.high), pool.inner) + buffer
        )
    risk = abs(entry - stop)
    if entry <= 0.0 or risk <= tick or (event.side > 0 and stop >= entry) or (event.side < 0 and stop <= entry):
        return None

    target_pool_info = _active_target_pool(pools, event.side, entry, decision_ts, pool.pool_id)
    target_pool: LiquidityPool | None = None
    target_id: str | None = None
    target: float | None = None
    if target_pool_info is not None:
        target_pool, target = target_pool_info
        target_id = target_pool.pool_id
    else:
        pivot_info = nearest_opposing_target(pivots, event.side, entry, decision_ts)
        if pivot_info is not None:
            target_id = pivot_info[0].pivot_id
            target = float(pivot_info[1])
    if target is None or (event.side > 0 and target <= entry) or (event.side < 0 and target >= entry):
        return None

    structural_econ = economics(event.side, entry, stop, target, tick)
    if structural_econ["fixed_risk_win_r"] <= 0.0:
        return None

    prior_sigma = max(float(frame.loc[decision_ts, "prior_sigma"]), 1e-12)
    interaction_depth = (
        (pool.outer - event.sweep_extreme) / entry / prior_sigma
        if pool.side == "LOW"
        else (event.sweep_extreme - pool.outer) / entry / prior_sigma
    )
    row: dict[str, Any] = {
        "plan_id": f"LT:{symbol}:{pool.pool_id}:{event.state}:{int(event.interaction_ts.value)}",
        "causal_event_id": f"{symbol}:{pool.pool_id}:{int(event.interaction_ts.value)}",
        "symbol": symbol,
        "family": "LIQUIDITY_TRANSFER_REVERSAL" if event.state == "SWEEP_RECLAIM" else "LIQUIDITY_ACCEPTANCE_CONTINUATION",
        "state": event.state,
        "side": "LONG" if event.side > 0 else "SHORT",
        "side_sign": event.side,
        "interaction_ts": event.interaction_ts.isoformat(),
        "confirm_ts": event.confirm_ts.isoformat(),
        "fvg_completion_ts": fvg.completion_ts.isoformat(),
        "decision_ts": decision_ts.isoformat(),
        "entry_ts": entry_ts.isoformat(),
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_bps": risk / entry * 1e4,
        "risk_sigma": risk / entry / prior_sigma,
        "cost_burden_r": abs(structural_econ["loss_net_price_r"] + 1.0),
        "structural_target_id": target_id,
        "structural_target_pool_timeframe": None if target_pool is None else target_pool.timeframe,
        "structural_gross_rr": abs(target - entry) / risk,
        "pool_id": pool.pool_id,
        "pool_side": pool.side,
        "pool_timeframe": pool.timeframe,
        "pool_span": pool.span,
        "pool_outer": pool.outer,
        "pool_inner": pool.inner,
        "pool_equality_atr": pool.equality_atr,
        "pool_strength": pool.strength,
        "pool_separation_bars": pool.separation_bars,
        "pool_age_minutes": (event.interaction_ts - pool.observed_ts) / pd.Timedelta(minutes=1),
        "interaction_depth_sigma": interaction_depth,
        "state_confirmation_minutes": (event.confirm_ts - event.interaction_ts) / pd.Timedelta(minutes=1),
        "fvg_delay_minutes": (fvg.completion_ts - event.confirm_ts) / pd.Timedelta(minutes=1),
        "mitigation_delay_minutes": (decision_ts - fvg.completion_ts) / pd.Timedelta(minutes=1),
        "fvg_lower": fvg.lower,
        "fvg_upper": fvg.upper,
        "fvg_gap_bps": fvg.gap_bps,
        "fvg_middle_range_ratio": fvg.middle_range_ratio,
        "fvg_middle_body_aligned": fvg.middle_body_aligned,
        "fvg_impulse_progress_sigma": fvg.impulse_progress_sigma,
        "mitigation_penetration": (
            max(fvg.upper - float(mitigation.low), 0.0) / max(fvg.upper - fvg.lower, tick)
            if event.side > 0
            else max(float(mitigation.high) - fvg.lower, 0.0) / max(fvg.upper - fvg.lower, tick)
        ),
        "mitigation_close_location_aligned": (
            float(mitigation.close_location) if event.side > 0 else 1.0 - float(mitigation.close_location)
        ),
        "mitigation_delta_aligned": event.side * float(mitigation.delta_share_1),
        "mitigation_range_ratio": float(mitigation.range_ratio),
        "mitigation_activity_ratio": float(mitigation.activity_ratio),
    }
    row.update(snapshot_features(frame, decision_ts, event.side))
    row.update(_pre_context(frame, bars5, event.interaction_ts, event.side))
    row.update(_event_flow_features(frame, event.interaction_ts - pd.Timedelta(minutes=4), event.confirm_ts, event.side))
    row.update(_metric_snapshot(frame, event.interaction_ts, "interaction"))
    row.update(_metric_snapshot(frame, event.confirm_ts, "confirm"))
    row.update(_metric_snapshot(frame, decision_ts, "decision"))
    oi_event = _metric_change(frame, event.interaction_ts - pd.Timedelta(minutes=15), event.confirm_ts)
    if oi_event is not None:
        row["event_oi_value_change"] = oi_event
        row["deleveraging_intensity"] = -oi_event
    oi_mitigation = _metric_change(frame, event.confirm_ts, decision_ts)
    if oi_mitigation is not None:
        row["post_confirm_oi_value_change"] = oi_mitigation
    row.update(_path_labels(frame, event.side, entry_i, entry, risk))
    row.update({f"structural_{key}": value for key, value in _barrier_label(frame, event.side, entry_i, entry, stop, target, tick).items()})
    for r in R_TARGETS:
        tag = str(r).replace(".", "p")
        objective = entry + event.side * risk * r
        labelled = _barrier_label(frame, event.side, entry_i, entry, stop, objective, tick)
        row[f"r_{tag}_target"] = objective
        for key, value in labelled.items():
            row[f"r_{tag}_{key}"] = value
    return row


def harvest_symbol(
    symbol: str,
    frame: pd.DataFrame,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    pools15, pivots15 = build_liquidity_pools(frame, 15)
    pools60, pivots60 = build_liquidity_pools(frame, 60)
    pools = sorted(pools15 + pools60, key=lambda pool: (pool.observed_ts, pool.timeframe, pool.pool_id))
    pivots = sorted(pivots15 + pivots60, key=lambda pivot: (pivot.observed_ts, pivot.timeframe, pivot.pivot_id))
    bars5 = aggregate(frame, 5)
    rows: list[dict[str, Any]] = []
    diagnostics = {
        "pools_15m": len(pools15),
        "pools_60m": len(pools60),
        "interactions": 0,
        "classified": 0,
        "plans": 0,
    }
    for pool in pools:
        if pool.first_interaction_ts is None:
            continue
        diagnostics["interactions"] += 1
        event = classify_first_interaction(pool, bars5, TICKS[symbol])
        if event is None:
            continue
        diagnostics["classified"] += 1
        plan = build_plan(symbol, frame, bars5, pools, pivots, pool, event, start_ts, end_ts)
        if plan is not None:
            rows.append(plan)
            diagnostics["plans"] += 1
    return rows, diagnostics


def _summary(events: pd.DataFrame, start: date, end: date, diagnostics: dict[str, Any]) -> dict[str, Any]:
    days = (end - start).days + 1
    summary: dict[str, Any] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": int(len(events)),
        "events_per_day": float(len(events) / days),
        "diagnostics": diagnostics,
        "research_only": "future barrier labels and MFE/MAE are never live features",
    }
    if events.empty:
        return summary
    summary["risk_bps"] = {
        "median": float(events.risk_bps.median()),
        "p10": float(events.risk_bps.quantile(0.10)),
        "p90": float(events.risk_bps.quantile(0.90)),
    }
    summary["by_family"] = {}
    for family, group in events.groupby("family"):
        item: dict[str, Any] = {"rows": int(len(group))}
        for objective in ("structural", "r_0p75", "r_1p0", "r_1p25", "r_1p5", "r_2p0"):
            label = f"{objective}_label"
            net = f"{objective}_net_r"
            resolved = group[label].notna()
            item[objective] = {
                "resolved": int(resolved.sum()),
                "target_first_rate": None if not resolved.any() else float(group.loc[resolved, label].mean()),
                "mean_net_r": None if not resolved.any() else float(group.loc[resolved, net].mean()),
                "median_net_r": None if not resolved.any() else float(group.loc[resolved, net].median()),
            }
        summary["by_family"][family] = item
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=21)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    warmup_start = args.start - timedelta(days=args.warmup_days)
    start_ts = pd.Timestamp(args.start, tz="UTC")
    end_ts = pd.Timestamp(args.end + timedelta(days=1), tz="UTC") - pd.Timedelta(minutes=1)
    raw_frames = {
        symbol: load_range_flow(symbol, warmup_start, args.end, args.cache / "klines")
        for symbol in SYMBOLS
    }
    frames = {symbol: make_features(symbol, raw) for symbol, raw in raw_frames.items()}
    frames = add_cross_features(frames)
    for symbol in SYMBOLS:
        metrics = load_metrics_range(symbol, warmup_start, args.end, args.cache)
        frames[symbol] = join_metrics_causally(frames[symbol], metrics)
    rows: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    for symbol in SYMBOLS:
        symbol_rows, symbol_diag = harvest_symbol(symbol, frames[symbol], start_ts, end_ts)
        rows.extend(symbol_rows)
        diagnostics[symbol] = symbol_diag
    events = pd.DataFrame(rows)
    if not events.empty:
        events = events.sort_values(["entry_ts", "symbol", "causal_event_id"]).reset_index(drop=True)
        if events["causal_event_id"].duplicated().any():
            duplicate = events.loc[events["causal_event_id"].duplicated(), "causal_event_id"].head().tolist()
            raise RuntimeError(f"duplicate causal events: {duplicate}")
    args.output.mkdir(parents=True, exist_ok=True)
    events.to_csv(args.output / "events.csv", index=False)
    (args.output / "summary.json").write_text(
        json.dumps(_summary(events, args.start, args.end, diagnostics), indent=2, sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
