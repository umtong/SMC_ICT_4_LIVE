#!/usr/bin/env python3
"""Causal mature-liquidity auction response study.

The study translates the EasyChart material into one reusable market decision:
a visible, repeatedly defended auction boundary is interacted with for the first
time after maturity; completed price/flow response classifies the interaction as
bounce, rejection/trap, or accepted break; only a later first retest can create
an entry.  Event, response and retest features are frozen before the next-open
entry.  Multiple R objectives are labelled after generation for research only.

This file is deliberately an event study, not a live policy.  Future barrier
labels and counterfactual objectives must never be imported by a strategy.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
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
from data_re1_flow import load_range_flow

R_TARGETS = (0.75, 1.00, 1.25, 1.50, 2.00, 2.50, 3.00)
MAX_HOLD_MINUTES = 360


@dataclass(frozen=True)
class DefenseLevel:
    level_id: str
    timeframe: int
    span: int
    side: str
    lower: float
    upper: float
    invalidation: float
    first_pivot_id: str
    second_pivot_id: str
    opposite_pivot_id: str
    first_event_ts: pd.Timestamp
    second_event_ts: pd.Timestamp
    observed_ts: pd.Timestamp
    strength: float
    separation_bars: int
    first_interaction_ts: pd.Timestamp | None


def _pivot_band(bars: pd.DataFrame, pivot: Pivot) -> tuple[float, float]:
    bar = bars.loc[pivot.event_ts]
    body_low = min(float(bar.open), float(bar.close))
    body_high = max(float(bar.open), float(bar.close))
    if pivot.side == "LOW":
        return float(bar.low), body_low
    return body_high, float(bar.high)


def _opposite_between(pivots: list[Pivot], first: Pivot, second: Pivot) -> Pivot | None:
    wanted = "HIGH" if first.side == "LOW" else "LOW"
    candidates = [
        p for p in pivots
        if p.timeframe == first.timeframe
        and p.side == wanted
        and first.event_ts < p.event_ts < second.event_ts
        and p.observed_ts <= second.observed_ts
    ]
    return max(candidates, key=lambda p: (p.span, p.strength, p.event_ts, p.pivot_id), default=None)


def _first_interaction(
    bars: pd.DataFrame,
    observed_ts: pd.Timestamp,
    side: str,
    lower: float,
    upper: float,
) -> pd.Timestamp | None:
    later = bars.loc[bars.index > observed_ts]
    if later.empty:
        return None
    touched = later.low.le(upper) if side == "LOW" else later.high.ge(lower)
    hits = np.flatnonzero(touched.to_numpy(bool))
    return None if not len(hits) else later.index[int(hits[0])]


def build_defense_levels(
    frame: pd.DataFrame,
    pivots: list[Pivot],
    timeframe: int,
    tick: float,
) -> list[DefenseLevel]:
    bars = aggregate(frame, timeframe)
    candidates = [p for p in pivots if p.timeframe == timeframe]
    claimed: set[str] = set()
    levels: list[DefenseLevel] = []
    for second in sorted(candidates, key=lambda p: (p.observed_ts, p.event_ts, p.pivot_id)):
        if second.pivot_id in claimed:
            continue
        second_lower, second_upper = _pivot_band(bars, second)
        compatible: list[tuple[Pivot, Pivot, float, float]] = []
        for first in candidates:
            if (
                first.pivot_id in claimed
                or first.pivot_id == second.pivot_id
                or first.side != second.side
                or first.span != second.span
                or first.event_ts >= second.event_ts
            ):
                continue
            opposite = _opposite_between(candidates, first, second)
            if opposite is None:
                continue
            first_lower, first_upper = _pivot_band(bars, first)
            lower = max(first_lower, second_lower)
            upper = min(first_upper, second_upper)
            if upper - lower + 1e-12 < tick:
                continue
            compatible.append((first, opposite, lower, upper))
        if not compatible:
            continue
        first, opposite, lower, upper = max(
            compatible,
            key=lambda item: (item[0].event_ts, item[0].strength, item[0].pivot_id),
        )
        invalidation = (
            min(first.price, second.price) - tick
            if second.side == "LOW"
            else max(first.price, second.price) + tick
        )
        interaction = _first_interaction(
            bars,
            max(first.observed_ts, opposite.observed_ts, second.observed_ts),
            second.side,
            lower,
            upper,
        )
        level = DefenseLevel(
            level_id=(
                f"{timeframe}m:{second.side}:s{second.span}:"
                f"{first.pivot_id}|{opposite.pivot_id}|{second.pivot_id}"
            ),
            timeframe=timeframe,
            span=second.span,
            side=second.side,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            first_pivot_id=first.pivot_id,
            second_pivot_id=second.pivot_id,
            opposite_pivot_id=opposite.pivot_id,
            first_event_ts=first.event_ts,
            second_event_ts=second.event_ts,
            observed_ts=max(first.observed_ts, opposite.observed_ts, second.observed_ts),
            strength=min(first.strength, second.strength),
            separation_bars=int((second.event_ts - first.event_ts) / pd.Timedelta(minutes=timeframe)),
            first_interaction_ts=interaction,
        )
        claimed.update((first.pivot_id, second.pivot_id))
        levels.append(level)
    return levels


def _active(level: DefenseLevel, ts: pd.Timestamp) -> bool:
    return level.observed_ts < ts and (
        level.first_interaction_ts is None or level.first_interaction_ts > ts
    )


def _nearest_active_opposite(
    levels: list[DefenseLevel],
    side: int,
    entry: float,
    ts: pd.Timestamp,
) -> DefenseLevel | None:
    wanted = "HIGH" if side > 0 else "LOW"
    choices = [
        level for level in levels
        if level.side == wanted
        and _active(level, ts)
        and ((side > 0 and level.lower > entry) or (side < 0 and level.upper < entry))
    ]
    if not choices:
        return None
    if side > 0:
        return min(choices, key=lambda level: (level.lower, -level.timeframe, -level.span, level.level_id))
    return max(choices, key=lambda level: (level.upper, level.timeframe, level.span, level.level_id))


def _bar_flow(bar: pd.Series) -> tuple[float, float]:
    quote = max(float(bar.quote_volume), 0.0)
    signed = 2.0 * float(bar.taker_buy_quote_volume) - quote
    return quote, signed


def _window_features(
    one: pd.DataFrame,
    five: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    side: int,
    prefix: str,
) -> dict[str, float]:
    out: dict[str, float] = {}
    minute = one.loc[(one.index > start) & (one.index <= end)]
    if minute.empty:
        return out
    q = minute.quote_volume.astype(float).clip(lower=0.0)
    signed = 2.0 * minute.taker_buy_quote_volume.astype(float) - q
    start_price = float(minute.open.iloc[0])
    end_price = float(minute.close.iloc[-1])
    log_returns = np.log(minute.close.astype(float).clip(lower=1e-12)).diff().fillna(
        math.log(max(end_price, 1e-12) / max(start_price, 1e-12))
    )
    net = side * math.log(max(end_price, 1e-12) / max(start_price, 1e-12))
    total = float(log_returns.abs().sum())
    out[f"{prefix}_minutes"] = float(len(minute))
    out[f"{prefix}_price_progress"] = net
    out[f"{prefix}_path_efficiency"] = net / max(total, 1e-12)
    out[f"{prefix}_delta_share"] = side * float(signed.sum()) / max(float(q.sum()), 1e-12)
    out[f"{prefix}_activity_mean"] = float(minute.activity_ratio.mean())
    out[f"{prefix}_activity_max"] = float(minute.activity_ratio.max())
    out[f"{prefix}_range_ratio_mean"] = float(minute.range_ratio.mean())
    out[f"{prefix}_adverse_absorption"] = max(-out[f"{prefix}_delta_share"], 0.0) * max(net, 0.0)
    return out


def _pre_features(frame: pd.DataFrame, bars5: pd.DataFrame, ts: pd.Timestamp) -> dict[str, float]:
    before = bars5.loc[bars5.index < ts]
    out: dict[str, float] = {}
    if len(before) >= 42:
        recent = (before.high - before.low).tail(6)
        prior = (before.high - before.low).iloc[-42:-6]
        out["pre_contraction_ratio"] = float(recent.median() / max(float(prior.median()), 1e-12))
        out["pre_range_trend"] = float(recent.mean() / max(float(prior.mean()), 1e-12))
        out["pre_location_in_36bar_range"] = float(
            (before.close.iloc[-1] - before.low.iloc[-36:].min())
            / max(float(before.high.iloc[-36:].max() - before.low.iloc[-36:].min()), 1e-12)
        )
    minute = frame.loc[frame.index < ts].tail(60)
    if len(minute) >= 15:
        r = np.log(minute.close.astype(float).clip(lower=1e-12)).diff().dropna()
        out["pre_realized_vol_60"] = float(np.sqrt(np.square(r).sum()))
        out["pre_path_eff_60"] = float(
            abs(math.log(float(minute.close.iloc[-1]) / float(minute.open.iloc[0])))
            / max(float(r.abs().sum()), 1e-12)
        )
    return out


def _classify_state(
    level: DefenseLevel,
    bars5: pd.DataFrame,
) -> tuple[str, int, pd.Timestamp, pd.Timestamp, float, float] | None:
    ts = level.first_interaction_ts
    if ts is None or ts not in bars5.index:
        return None
    i = bars5.index.get_loc(ts)
    if isinstance(i, slice) or i >= len(bars5) - 1:
        return None
    event = bars5.iloc[i]
    nxt = bars5.iloc[i + 1]
    if level.side == "LOW":
        swept = float(event.low) < level.lower
        inside = float(event.close) > level.upper
        outside = float(event.close) < level.lower
        next_inside = float(nxt.close) > level.upper
        next_hold = float(nxt.open) < level.lower and float(nxt.close) < level.lower
        event_extreme = min(float(event.low), float(nxt.low))
        if inside:
            return ("SWEEP_REJECTION" if swept else "DEFENSE_BOUNCE", 1, ts, ts, float(event.low), float(event.high))
        if outside and next_inside:
            return ("DELAYED_TRAP_REJECTION", 1, ts, bars5.index[i + 1], event_extreme, max(float(event.high), float(nxt.high)))
        if outside and next_hold:
            return ("ACCEPTED_BREAK", -1, ts, bars5.index[i + 1], event_extreme, max(float(event.high), float(nxt.high)))
        return None
    swept = float(event.high) > level.upper
    inside = float(event.close) < level.lower
    outside = float(event.close) > level.upper
    next_inside = float(nxt.close) < level.lower
    next_hold = float(nxt.open) > level.upper and float(nxt.close) > level.upper
    event_extreme = max(float(event.high), float(nxt.high))
    if inside:
        return ("SWEEP_REJECTION" if swept else "DEFENSE_BOUNCE", -1, ts, ts, float(event.low), float(event.high))
    if outside and next_inside:
        return ("DELAYED_TRAP_REJECTION", -1, ts, bars5.index[i + 1], min(float(event.low), float(nxt.low)), event_extreme)
    if outside and next_hold:
        return ("ACCEPTED_BREAK", 1, ts, bars5.index[i + 1], min(float(event.low), float(nxt.low)), event_extreme)
    return None


def _find_retest(
    frame: pd.DataFrame,
    level: DefenseLevel,
    state: str,
    side: int,
    decision_ts: pd.Timestamp,
    event_low: float,
    event_high: float,
) -> int | None:
    idx = frame.index
    start = idx.searchsorted(decision_ts + pd.Timedelta(minutes=1), side="left")
    end = min(start + (121 if state == "ACCEPTED_BREAK" else 61), len(frame) - 1)
    for i in range(start, end):
        bar = frame.iloc[i]
        if state == "ACCEPTED_BREAK":
            if side > 0:
                invalid = float(bar.close) < level.lower
                touched = float(bar.low) <= level.upper and float(bar.close) > level.upper
            else:
                invalid = float(bar.close) > level.upper
                touched = float(bar.high) >= level.lower and float(bar.close) < level.lower
        else:
            if side > 0:
                invalid = float(bar.low) <= event_low - TICKS[str(bar.symbol)] if "symbol" in bar else float(bar.low) <= event_low
                touched = float(bar.low) <= level.upper and float(bar.close) > level.upper
            else:
                invalid = float(bar.high) >= event_high + TICKS[str(bar.symbol)] if "symbol" in bar else float(bar.high) >= event_high
                touched = float(bar.high) >= level.lower and float(bar.close) < level.lower
        if invalid:
            return None
        if touched:
            return i
    return None


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
    future = frame.iloc[entry_i: min(entry_i + MAX_HOLD_MINUTES, len(frame))]
    outcome = "UNRESOLVED"
    resolution_ts: pd.Timestamp | None = None
    for ts, bar in future.iterrows():
        stop_hit = float(bar.low) <= stop if side > 0 else float(bar.high) >= stop
        target_hit = float(bar.high) >= target if side > 0 else float(bar.low) <= target
        if stop_hit:
            outcome = "AMBIGUOUS_SAME_MINUTE" if target_hit else "STOP_FIRST"
            resolution_ts = ts
            break
        if target_hit:
            outcome = "TARGET_FIRST"
            resolution_ts = ts
            break
    label = 1.0 if outcome == "TARGET_FIRST" else 0.0 if outcome in {"STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"} else np.nan
    net_r = econ["fixed_risk_win_r"] if label == 1.0 else -1.0 if label == 0.0 else np.nan
    return {
        **econ,
        "outcome": outcome,
        "label": label,
        "net_r": net_r,
        "resolution_ts": None if resolution_ts is None else resolution_ts.isoformat(),
        "minutes_to_resolution": None if resolution_ts is None else int((resolution_ts - frame.index[entry_i]) / pd.Timedelta(minutes=1)),
    }


def _path_labels(frame: pd.DataFrame, side: int, entry_i: int, entry: float, risk: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for horizon in (5, 15, 30, 60, 120, 240):
        future = frame.iloc[entry_i:min(entry_i + horizon, len(frame))]
        if future.empty:
            continue
        if side > 0:
            mfe = (float(future.high.max()) - entry) / risk
            mae = (entry - float(future.low.min())) / risk
        else:
            mfe = (entry - float(future.low.min())) / risk
            mae = (float(future.high.max()) - entry) / risk
        out[f"mfe_r_{horizon}"] = mfe
        out[f"mae_r_{horizon}"] = mae
    return out


def _event_row(
    symbol: str,
    frame: pd.DataFrame,
    bars5: pd.DataFrame,
    pivots: list[Pivot],
    levels: list[DefenseLevel],
    level: DefenseLevel,
    start_ts: pd.Timestamp,
) -> dict[str, Any] | None:
    classified = _classify_state(level, bars5)
    if classified is None:
        return None
    state, side, interaction_ts, decision_base_ts, event_low, event_high = classified
    if interaction_ts < start_ts:
        return None
    retest_i = _find_retest(frame, level, state, side, decision_base_ts, event_low, event_high)
    if retest_i is None or retest_i >= len(frame) - 1:
        return None
    retest = frame.iloc[retest_i]
    decision_ts = frame.index[retest_i]
    entry_i = retest_i + 1
    entry_ts = frame.index[entry_i]
    entry = float(frame.iloc[entry_i].open)
    tick = TICKS[symbol]
    if state == "ACCEPTED_BREAK":
        stop = (
            min(float(retest.low), level.lower) - tick
            if side > 0
            else max(float(retest.high), level.upper) + tick
        )
    else:
        stop = min(event_low, float(retest.low)) - tick if side > 0 else max(event_high, float(retest.high)) + tick
    risk = abs(entry - stop)
    if entry <= 0.0 or risk <= tick * 0.5:
        return None
    if side > 0 and stop >= entry:
        return None
    if side < 0 and stop <= entry:
        return None

    opposite = _nearest_active_opposite(levels, side, entry, decision_ts)
    structural_target: float | None = None
    target_level_id: str | None = None
    if opposite is not None:
        structural_target = opposite.lower if side > 0 else opposite.upper
        target_level_id = opposite.level_id
    else:
        pivot_target = nearest_opposing_target(pivots, side, entry, decision_ts)
        if pivot_target is not None:
            _, structural_target = pivot_target
            target_level_id = pivot_target[0].pivot_id
    if structural_target is not None:
        if side > 0 and structural_target <= entry:
            structural_target = None
        if side < 0 and structural_target >= entry:
            structural_target = None

    sigma = max(float(frame.loc[decision_ts, "prior_sigma"]), 1e-12)
    row: dict[str, Any] = {
        "plan_id": f"AR:{symbol}:{level.level_id}:{state}:{int(interaction_ts.value)}",
        "causal_event_id": f"{symbol}:{level.level_id}:{int(interaction_ts.value)}",
        "symbol": symbol,
        "state": state,
        "family": "MATURE_LIQUIDITY_AUCTION_RESPONSE",
        "side": "LONG" if side > 0 else "SHORT",
        "side_sign": side,
        "interaction_ts": interaction_ts.isoformat(),
        "state_decision_ts": decision_base_ts.isoformat(),
        "decision_ts": decision_ts.isoformat(),
        "entry_ts": entry_ts.isoformat(),
        "entry": entry,
        "stop": stop,
        "risk_bps": risk / entry * 1e4,
        "risk_sigma": risk / entry / sigma,
        "level_id": level.level_id,
        "level_side": level.side,
        "level_timeframe": level.timeframe,
        "level_span": level.span,
        "level_lower": level.lower,
        "level_upper": level.upper,
        "level_width_bps": (level.upper - level.lower) / entry * 1e4,
        "level_width_sigma": (level.upper - level.lower) / entry / sigma,
        "level_strength": level.strength,
        "level_separation_bars": level.separation_bars,
        "level_age_minutes": (interaction_ts - level.observed_ts) / pd.Timedelta(minutes=1),
        "level_observed_ts": level.observed_ts.isoformat(),
        "interaction_depth_sigma": (
            (level.lower - event_low) / entry / sigma
            if level.side == "LOW"
            else (event_high - level.upper) / entry / sigma
        ),
        "retest_delay_minutes": (decision_ts - decision_base_ts) / pd.Timedelta(minutes=1),
        "retest_penetration_sigma": (
            max(level.upper - float(retest.low), 0.0) / entry / sigma
            if side > 0
            else max(float(retest.high) - level.lower, 0.0) / entry / sigma
        ),
        "structural_target": structural_target,
        "structural_target_level_id": target_level_id,
        "structural_gross_rr": None if structural_target is None else abs(structural_target - entry) / risk,
    }
    row.update(snapshot_features(frame, decision_ts, side))
    row.update(_pre_features(frame, bars5, interaction_ts))
    # Completed interaction bar(s), response to retest, and the retest itself.
    event_bar = bars5.loc[interaction_ts]
    event_quote, event_signed = _bar_flow(event_bar)
    event_range = max(float(event_bar.high - event_bar.low), 1e-12)
    row.update({
        "event_range_bps": event_range / float(event_bar.close) * 1e4,
        "event_body_fraction_aligned": side * float(event_bar.close - event_bar.open) / event_range,
        "event_close_location_aligned": (
            float(event_bar.close - event_bar.low) / event_range
            if side > 0
            else float(event_bar.high - event_bar.close) / event_range
        ),
        "event_delta_share_aligned": side * event_signed / max(event_quote, 1e-12),
        "event_quote_volume": event_quote,
        "event_reclaim_fraction": (
            float(event_bar.close - event_bar.low) / event_range
            if side > 0
            else float(event_bar.high - event_bar.close) / event_range
        ),
        "retest_range_ratio": float(retest.range_ratio),
        "retest_activity_ratio": float(retest.activity_ratio),
        "retest_trade_count_ratio": float(retest.trade_count_ratio),
        "retest_delta_share_aligned": side * float(retest.delta_share_1),
        "retest_body_fraction_aligned": side * float(retest.body_fraction),
        "retest_close_location_aligned": (
            float(retest.close_location) if side > 0 else 1.0 - float(retest.close_location)
        ),
    })
    row.update(_window_features(frame, bars5, interaction_ts - pd.Timedelta(minutes=5), decision_base_ts, side, "event_phase"))
    row.update(_window_features(frame, bars5, decision_base_ts, decision_ts, side, "retest_phase"))
    row.update(_path_labels(frame, side, entry_i, entry, risk))

    # Structural objective and several research-only planned R objectives.
    if structural_target is not None:
        row.update({f"structural_{k}": v for k, v in _barrier_label(frame, side, entry_i, entry, stop, structural_target, tick).items()})
    for r in R_TARGETS:
        tag = str(r).replace(".", "p")
        target = entry + side * risk * r
        labelled = _barrier_label(frame, side, entry_i, entry, stop, target, tick)
        row[f"r_{tag}_target"] = target
        for key, value in labelled.items():
            row[f"r_{tag}_{key}"] = value
    return row


def harvest_symbol(
    symbol: str,
    frame: pd.DataFrame,
    start_ts: pd.Timestamp,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    tick = TICKS[symbol]
    pivots = confirmed_pivots(frame, 5, (2, 4)) + confirmed_pivots(frame, 15, (2, 4))
    pivots = sorted(pivots, key=lambda p: (p.observed_ts, p.timeframe, p.span, p.pivot_id))
    levels = build_defense_levels(frame, pivots, 5, tick) + build_defense_levels(frame, pivots, 15, tick)
    bars5 = aggregate(frame, 5)
    rows: list[dict[str, Any]] = []
    for level in levels:
        row = _event_row(symbol, frame, bars5, pivots, levels, level, start_ts)
        if row is not None:
            rows.append(row)
    return rows, {"pivots": len(pivots), "levels": len(levels)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--warmup-days", type=int, default=14)
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    load_start = args.start - timedelta(days=args.warmup_days)
    raw = {symbol: load_range_flow(symbol, load_start, args.end, args.cache) for symbol in SYMBOLS}
    frames = add_cross_features({symbol: make_features(symbol, value) for symbol, value in raw.items()})
    start_ts = pd.Timestamp(args.start, tz="UTC")
    rows: list[dict[str, Any]] = []
    diagnostics: dict[str, dict[str, int]] = {}
    for symbol, frame in frames.items():
        produced, diag = harvest_symbol(symbol, frame, start_ts)
        rows.extend(produced)
        diagnostics[symbol] = diag
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(
            ["entry_ts", "symbol", "state", "level_observed_ts", "level_strength", "plan_id"],
            ascending=[True, True, True, False, False, True],
            kind="mergesort",
        )
        out = out.drop_duplicates(["symbol", "interaction_ts", "state", "side"], keep="first")
    out.to_csv(args.output / "events.csv", index=False)
    by_state: dict[str, Any] = {}
    if not out.empty:
        for state, group in out.groupby("state"):
            state_summary: dict[str, Any] = {"rows": int(len(group))}
            for r in R_TARGETS:
                tag = str(r).replace(".", "p")
                label = f"r_{tag}_label"
                net = f"r_{tag}_net_r"
                state_summary[f"r_{tag}"] = {
                    "resolved": int(group[label].notna().sum()),
                    "target_first_rate": float(group[label].mean()),
                    "mean_net_r": float(group[net].mean()),
                }
            by_state[state] = state_summary
    days = (args.end - args.start).days + 1
    summary = {
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "rows": int(len(out)),
        "events_per_day": float(len(out) / days),
        "diagnostics": diagnostics,
        "by_state": by_state,
        "research_only": "future barrier labels and counterfactual R objectives are not live features",
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
