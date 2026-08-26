"""Causal auction-episode action and geometry research.

This module intentionally does not filter an existing trading policy.  It starts
from pre-existing auction boundaries, observes what price and aggressor flow do
at those boundaries, then enumerates mutually exclusive executable actions:

* failed auction: penetration, reclaim, local control transfer and optional retest;
* accepted auction: close outside, hold, local response and optional retest;
* abstention is represented by leaving all actions unselected later.

Every emitted action fixes entry, invalidation, objective and order expiry using
only information available at emission.  Limit entries are labelled only after a
causal future fill; they are never treated as if entry happened at signal time.
Future bars are used solely by the offline first-passage labeler.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd


NS_PER_MINUTE = 60_000_000_000
SOURCE_TIMEFRAMES = (15, 60)
OBJECTIVE_TIMEFRAMES = (5, 15, 60)
PIVOT_SPANS: dict[int, tuple[int, ...]] = {
    5: (2, 4),
    15: (2, 4),
    60: (2,),
}
MAKER_FEE_RATE = 0.0002
TAKER_FEE_RATE = 0.0005
ENTRY_SLIPPAGE_TICKS = 2
STOP_SLIPPAGE_TICKS = 2
LIMIT_TRADE_THROUGH_TICKS = 1

CAUSAL_STRUCTURE_POLICY = (
    "CAUSAL_AUCTION:WICK_PIVOTS_ARE_CONFIRMED_ONLY_AFTER_THE_RIGHT_HAND_SPAN_"
    "CLOSES_AND_A_SOURCE_BOUNDARY_MUST_EXIST_BEFORE_ITS_INTERACTION_BAR"
)
ACTION_POLICY = (
    "CAUSAL_AUCTION:FAILED_AND_ACCEPTED_AUCTIONS_OWN_DISTINCT_ACTIONS;EACH_ACTION_"
    "FIXES_ENTRY_INVALIDATION_OBJECTIVE_AND_EXPIRY_BEFORE_FUTURE_LABELS"
)
LIMIT_FILL_POLICY = (
    "CONSERVATIVE_EXECUTION:POST_ONLY_LIMIT_REQUIRES_ONE_TICK_TRADE_THROUGH;STOP_"
    "ON_FILL_BAR_IS_STOP_FIRST;TARGET_ON_FILL_BAR_IS_NOT_CREDITED"
)


@dataclass(frozen=True, slots=True)
class Contract:
    tick_size: float


CONTRACTS: dict[str, Contract] = {
    "BTCUSDT": Contract(0.1),
    "ETHUSDT": Contract(0.01),
    "SOLUSDT": Contract(0.001),
    "XRPUSDT": Contract(0.0001),
}


@dataclass(slots=True)
class PivotLevel:
    level_id: str
    symbol: str
    side: str  # HIGH or LOW
    timeframe_minutes: int
    span: int
    price: float
    lower: float
    upper: float
    event_time_ns: int
    observed_time_ns: int
    observed_index_1m: int
    strength_ratio: float
    defense_count: int
    first_touch_index_1m: int | None = None
    retired_as_source: bool = False

    @property
    def source_kind(self) -> str:
        return f"{self.timeframe_minutes}M_PIVOT_{self.side}"


@dataclass(frozen=True, slots=True)
class Objective:
    objective_id: str
    kind: str
    timeframe_minutes: int
    price: float
    strength_ratio: float


@dataclass(frozen=True, slots=True)
class ActionSpec:
    action_id: str
    episode_id: str
    symbol: str
    event_type: str
    decision_stage: str
    side: str
    emission_index: int
    emission_time_ns: int
    entry_style: str
    entry: float
    stop: float
    target: float
    entry_expiry_minutes: int
    source_level_id: str
    source_kind: str
    source_timeframe_minutes: int
    source_span: int
    source_price: float
    source_lower: float
    source_upper: float
    source_strength_ratio: float
    source_defense_count: int
    source_age_minutes: float
    objective_id: str
    objective_kind: str
    objective_timeframe_minutes: int
    objective_strength_ratio: float
    interaction_time_ns: int
    feature_values: dict[str, Any]


@dataclass(slots=True)
class EpisodeContext:
    episode_id: str
    boundary: PivotLevel
    interaction_index: int
    interaction_time_ns: int
    interaction_extreme: float
    approach: dict[str, float]
    emitted: set[tuple[str, str, str]] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class LabelResult:
    fill_state: str
    outcome: str
    fill_index: int | None
    fill_time_ns: int | None
    resolution_index: int | None
    resolution_time_ns: int | None
    entry_wait_minutes: float | None
    holding_minutes: float | None
    target_net_r: float
    stop_net_r: float
    net_r: float | None
    mfe_r: float | None
    mae_r: float | None


def _stable_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _time_ns(index: pd.DatetimeIndex, position: int) -> int:
    return int(index[position].value)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _median(values: Sequence[float], default: float) -> float:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return float(np.median(finite)) if finite else default


def _resample_flow(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    work = frame.copy()
    work.index = pd.DatetimeIndex(work.pop("open_time_dt")) + pd.Timedelta(minutes=1)
    aggregation = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "quote_volume": "sum",
        "count": "sum",
        "taker_buy_volume": "sum",
        "taker_buy_quote_volume": "sum",
    }
    output = work.resample(
        f"{minutes}min",
        label="right",
        closed="right",
        origin="epoch",
    ).agg(aggregation)
    output = output.dropna(subset=["open", "high", "low", "close"])
    output["count"] = output["count"].astype("int64")
    return output


def prepare_one_minute(frame: pd.DataFrame, tick_size: float) -> pd.DataFrame:
    data = frame.copy()
    data.index = pd.DatetimeIndex(data.pop("open_time_dt")) + pd.Timedelta(minutes=1)
    data = data.sort_index()
    quote = data["quote_volume"].astype(float).clip(lower=0.0)
    count = data["count"].astype(float).clip(lower=1.0)
    signed_quote = 2.0 * data["taker_buy_quote_volume"].astype(float) - quote
    body = data["close"].astype(float) - data["open"].astype(float)
    price_range = (data["high"] - data["low"]).astype(float).clip(lower=tick_size)
    log_close = np.log(data["close"].astype(float).clip(lower=tick_size))
    trade_size = quote / count

    prior_quote = quote.shift(1).rolling(60, min_periods=30).median().clip(lower=1e-12)
    prior_abs_delta = signed_quote.abs().shift(1).rolling(60, min_periods=30).median().clip(lower=1e-12)
    prior_abs_body = body.abs().shift(1).rolling(60, min_periods=30).median().clip(lower=tick_size)
    prior_range = price_range.shift(1).rolling(60, min_periods=30).median().clip(lower=tick_size)
    prior_trade_size = trade_size.shift(1).rolling(60, min_periods=30).median().clip(lower=1e-12)

    data["signed_quote"] = signed_quote
    data["delta_share"] = signed_quote / quote.replace(0.0, np.nan)
    data["body"] = body
    data["price_range"] = price_range
    data["close_location"] = (data["close"] - data["low"]) / price_range
    data["activity_ratio"] = quote / prior_quote
    data["delta_ratio"] = signed_quote.abs() / prior_abs_delta
    data["body_ratio"] = body.abs() / prior_abs_body
    data["range_ratio"] = price_range / prior_range
    data["trade_size_ratio"] = trade_size / prior_trade_size
    data["impact_per_activity"] = data["body_ratio"] / data["activity_ratio"].clip(lower=1e-12)
    data["prior_range_1m"] = prior_range
    for minutes in (3, 5, 15, 30, 60):
        data[f"return_{minutes}m"] = log_close.diff(minutes)
        q = quote.rolling(minutes, min_periods=minutes).sum()
        d = signed_quote.rolling(minutes, min_periods=minutes).sum()
        data[f"delta_share_{minutes}m"] = d / q.replace(0.0, np.nan)
        data[f"activity_{minutes}m"] = q / prior_quote.rolling(minutes, min_periods=1).sum().clip(lower=1e-12)
    return data.replace([np.inf, -np.inf], np.nan)


def detect_pivots(
    symbol: str,
    one_minute: pd.DataFrame,
    aggregates: dict[int, pd.DataFrame],
    tick_size: float,
) -> list[PivotLevel]:
    output: list[PivotLevel] = []
    one_index = one_minute.index
    histories: dict[tuple[int, str], list[PivotLevel]] = {}
    for timeframe in OBJECTIVE_TIMEFRAMES:
        bars = aggregates[timeframe]
        ranges = (bars["high"] - bars["low"]).astype(float)
        prior_atr = ranges.shift(1).rolling(20, min_periods=5).median()
        for span in PIVOT_SPANS[timeframe]:
            if len(bars) < 2 * span + 1:
                continue
            highs = bars["high"].to_numpy(dtype=float)
            lows = bars["low"].to_numpy(dtype=float)
            for center in range(span, len(bars) - span):
                window_high = highs[center - span : center + span + 1]
                window_low = lows[center - span : center + span + 1]
                observed_pos = center + span
                observed_time = bars.index[observed_pos]
                observed_1m = int(one_index.searchsorted(observed_time, side="left"))
                if observed_1m >= len(one_index):
                    continue
                atr = _finite(prior_atr.iloc[observed_pos], max(tick_size, ranges.iloc[center]))
                width = max(2.0 * tick_size, 0.06 * atr)
                for side in ("HIGH", "LOW"):
                    if side == "HIGH":
                        unique = highs[center] == window_high.max() and int((window_high == highs[center]).sum()) == 1
                        if not unique:
                            continue
                        prominence = min(
                            highs[center] - window_low[:span].min(),
                            highs[center] - window_low[span + 1 :].min(),
                        )
                        price = highs[center]
                    else:
                        unique = lows[center] == window_low.min() and int((window_low == lows[center]).sum()) == 1
                        if not unique:
                            continue
                        prominence = min(
                            window_high[:span].max() - lows[center],
                            window_high[span + 1 :].max() - lows[center],
                        )
                        price = lows[center]
                    strength = prominence / max(atr, tick_size)
                    key = (timeframe, side)
                    prior_levels = histories.setdefault(key, [])
                    tolerance = max(4.0 * tick_size, 0.18 * atr)
                    defense_count = 1 + sum(
                        1
                        for item in prior_levels[-24:]
                        if item.observed_time_ns < int(observed_time.value)
                        and abs(item.price - price) <= tolerance
                    )
                    level_id = (
                        f"{symbol}:{timeframe}m:{side}:c{center}:s{span}:"
                        f"{int(observed_time.value)}"
                    )
                    level = PivotLevel(
                        level_id=level_id,
                        symbol=symbol,
                        side=side,
                        timeframe_minutes=timeframe,
                        span=span,
                        price=float(price),
                        lower=float(price - width),
                        upper=float(price + width),
                        event_time_ns=int(bars.index[center].value),
                        observed_time_ns=int(observed_time.value),
                        observed_index_1m=observed_1m,
                        strength_ratio=float(strength),
                        defense_count=int(defense_count),
                    )
                    prior_levels.append(level)
                    output.append(level)
    output.sort(key=lambda item: (item.observed_time_ns, item.timeframe_minutes, item.level_id))

    highs_1m = one_minute["high"].to_numpy(dtype=float)
    lows_1m = one_minute["low"].to_numpy(dtype=float)
    for level in output:
        start = level.observed_index_1m + 1
        if start >= len(one_minute):
            continue
        mask = highs_1m[start:] >= level.price if level.side == "HIGH" else lows_1m[start:] <= level.price
        hits = np.flatnonzero(mask)
        if hits.size:
            level.first_touch_index_1m = int(start + hits[0])
    return output


def _approach_features(data: pd.DataFrame, index: int, boundary: PivotLevel) -> dict[str, float]:
    start = max(0, index - 30)
    prior = data.iloc[start:index]
    recent = data.iloc[max(start, index - 8) : index]
    if prior.empty:
        return {}
    closes = prior["close"].to_numpy(dtype=float)
    diffs = np.diff(closes)
    path = float(np.abs(diffs).sum())
    net = float(closes[-1] - closes[0]) if len(closes) > 1 else 0.0
    toward_sign = -1.0 if boundary.side == "LOW" else 1.0
    level = max(abs(boundary.price), 1e-12)
    distance_start = abs(closes[0] - boundary.price) / level * 10_000.0
    distance_end = abs(closes[-1] - boundary.price) / level * 10_000.0
    zone_tolerance = max(boundary.upper - boundary.lower, _finite(prior["prior_range_1m"].iloc[-1], 0.0))
    touches = int(
        (
            (prior["low"] <= boundary.upper + zone_tolerance)
            & (prior["high"] >= boundary.lower - zone_tolerance)
        ).sum()
    )
    return {
        "approach_net_bps": toward_sign * net / level * 10_000.0,
        "approach_path_efficiency": abs(net) / max(path, 1e-12),
        "approach_distance_start_bps": distance_start,
        "approach_distance_end_bps": distance_end,
        "approach_distance_compression": distance_start / max(distance_end, 1e-6),
        "approach_touch_count_30m": float(touches),
        "approach_range_ratio_8v30": (
            _median(recent["price_range"].tolist(), 0.0)
            / max(_median(prior["price_range"].tolist(), 1e-12), 1e-12)
        ),
        "approach_activity_ratio": _finite(recent["activity_ratio"].median(), 0.0),
        "approach_delta_share": _finite(recent["signed_quote"].sum() / max(recent["quote_volume"].sum(), 1e-12), 0.0),
        "approach_impact_per_activity": _finite(recent["impact_per_activity"].median(), 0.0),
    }


def _bar_features(row: pd.Series, prefix: str) -> dict[str, float]:
    fields = (
        "activity_ratio",
        "delta_ratio",
        "delta_share",
        "body_ratio",
        "range_ratio",
        "trade_size_ratio",
        "impact_per_activity",
        "close_location",
    )
    return {f"{prefix}_{field}": _finite(row.get(field), 0.0) for field in fields}


def _signed_progress(side: str, start: float, end: float) -> float:
    return end - start if side == "LONG" else start - end


def _side_sign(side: str) -> float:
    if side == "LONG":
        return 1.0
    if side == "SHORT":
        return -1.0
    raise ValueError(side)


def _source_reversal_side(boundary: PivotLevel) -> str:
    return "LONG" if boundary.side == "LOW" else "SHORT"


def _source_continuation_side(boundary: PivotLevel) -> str:
    return "SHORT" if boundary.side == "LOW" else "LONG"


def _objective_candidates(
    levels: Sequence[PivotLevel],
    *,
    side: str,
    entry: float,
    emission_index: int,
    emission_time_ns: int,
    micro_reference: float | None,
    tick_size: float,
) -> list[Objective]:
    wanted = "HIGH" if side == "LONG" else "LOW"
    candidates: list[Objective] = []
    if micro_reference is not None and (
        (side == "LONG" and micro_reference > entry + tick_size)
        or (side == "SHORT" and micro_reference < entry - tick_size)
    ):
        candidates.append(
            Objective(
                objective_id=f"MICRO:{emission_time_ns}:{micro_reference:.12g}",
                kind="PREINTERACTION_MICRO_CONTROL",
                timeframe_minutes=1,
                price=float(micro_reference),
                strength_ratio=1.0,
            ),
        )
    for level in levels:
        if level.side != wanted or level.observed_time_ns >= emission_time_ns:
            continue
        if level.first_touch_index_1m is not None and level.first_touch_index_1m <= emission_index:
            continue
        if side == "LONG" and level.price <= entry + tick_size:
            continue
        if side == "SHORT" and level.price >= entry - tick_size:
            continue
        candidates.append(
            Objective(
                objective_id=level.level_id,
                kind=level.source_kind,
                timeframe_minutes=level.timeframe_minutes,
                price=level.price,
                strength_ratio=level.strength_ratio,
            ),
        )
    candidates.sort(
        key=lambda item: (
            abs(item.price - entry),
            -item.timeframe_minutes,
            -item.strength_ratio,
            item.objective_id,
        ),
    )
    selected: list[Objective] = []
    for candidate in candidates:
        if any(abs(candidate.price - item.price) <= 2.0 * tick_size for item in selected):
            continue
        selected.append(candidate)
        if len(selected) >= 3:
            break
    return selected


def _economics(
    *,
    side: str,
    entry: float,
    stop: float,
    target: float,
    tick_size: float,
    entry_style: str,
) -> dict[str, float]:
    sign = _side_sign(side)
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0.0 or reward <= 0.0:
        return {}
    entry_fee = MAKER_FEE_RATE if entry_style != "MARKET" else TAKER_FEE_RATE
    entry_slip = 0 if entry_style != "MARKET" else ENTRY_SLIPPAGE_TICKS

    actual_entry = entry + sign * entry_slip * tick_size
    actual_target = target
    actual_stop = stop - sign * STOP_SLIPPAGE_TICKS * tick_size
    target_gross = sign * (actual_target - actual_entry) / risk
    stop_gross = sign * (actual_stop - actual_entry) / risk
    target_fees = (entry_fee * abs(actual_entry) + MAKER_FEE_RATE * abs(actual_target)) / risk
    stop_fees = (entry_fee * abs(actual_entry) + TAKER_FEE_RATE * abs(actual_stop)) / risk
    target_net = target_gross - target_fees
    stop_net = stop_gross - stop_fees
    denominator = target_net - stop_net
    break_even = -stop_net / denominator if denominator > 0.0 else float("nan")
    return {
        "gross_rr": reward / risk,
        "risk_bps": risk / abs(entry) * 10_000.0,
        "target_bps": reward / abs(entry) * 10_000.0,
        "target_net_r": target_net,
        "stop_net_r": stop_net,
        "post_cost_reward_risk": target_net / abs(stop_net) if target_net > 0.0 and stop_net < 0.0 else float("nan"),
        "post_cost_break_even_probability": break_even,
        "round_trip_cost_r_target": reward / risk - target_net,
    }


def _valid_geometry(side: str, entry: float, stop: float, target: float, tick_size: float) -> bool:
    if side == "LONG":
        return stop < entry - tick_size and target > entry + tick_size
    return stop > entry + tick_size and target < entry - tick_size


def _entry_levels(
    *,
    event_type: str,
    side: str,
    decision_close: float,
    boundary: PivotLevel,
    event_extreme: float,
    tick_size: float,
) -> list[tuple[str, str, float]]:
    output = [("MARKET", "MARKET", float(decision_close))]
    if side == "LONG":
        boundary_entry = boundary.upper if event_type == "FAILED_AUCTION" else boundary.lower
        anchor = event_extreme if event_type == "FAILED_AUCTION" else boundary.lower
        half = anchor + 0.5 * (decision_close - anchor)
        limit_values = (
            ("BOUNDARY_LIMIT", boundary_entry),
            ("IMPULSE_HALF_LIMIT", half),
        )
        for name, value in limit_values:
            value = min(float(value), decision_close - tick_size)
            if value > event_extreme + tick_size:
                output.append((name, "LIMIT", value))
    else:
        boundary_entry = boundary.lower if event_type == "FAILED_AUCTION" else boundary.upper
        anchor = event_extreme if event_type == "FAILED_AUCTION" else boundary.upper
        half = anchor - 0.5 * (anchor - decision_close)
        limit_values = (
            ("BOUNDARY_LIMIT", boundary_entry),
            ("IMPULSE_HALF_LIMIT", half),
        )
        for name, value in limit_values:
            value = max(float(value), decision_close + tick_size)
            if value < event_extreme - tick_size:
                output.append((name, "LIMIT", value))
    deduped: list[tuple[str, str, float]] = []
    for item in output:
        if any(abs(item[2] - prior[2]) <= tick_size for prior in deduped):
            continue
        deduped.append(item)
    return deduped


def _make_actions(
    *,
    data: pd.DataFrame,
    levels: Sequence[PivotLevel],
    context: EpisodeContext,
    event_type: str,
    decision_stage: str,
    side: str,
    emission_index: int,
    event_extreme: float,
    stop_reference: float,
    micro_reference: float | None,
    feature_values: dict[str, Any],
    tick_size: float,
) -> list[ActionSpec]:
    row = data.iloc[emission_index]
    decision_close = float(row["close"])
    buffer = max(2.0 * tick_size, 0.05 * _finite(row.get("prior_range_1m"), tick_size))
    stop = stop_reference - buffer if side == "LONG" else stop_reference + buffer
    expiry = 20 if context.boundary.timeframe_minutes <= 15 else 45
    actions: list[ActionSpec] = []
    emission_time_ns = _time_ns(data.index, emission_index)
    for entry_name, order_type, entry in _entry_levels(
        event_type=event_type,
        side=side,
        decision_close=decision_close,
        boundary=context.boundary,
        event_extreme=event_extreme,
        tick_size=tick_size,
    ):
        objectives = _objective_candidates(
            levels,
            side=side,
            entry=entry,
            emission_index=emission_index,
            emission_time_ns=emission_time_ns,
            micro_reference=micro_reference,
            tick_size=tick_size,
        )
        for objective_rank, objective in enumerate(objectives, start=1):
            if not _valid_geometry(side, entry, stop, objective.price, tick_size):
                continue
            economics = _economics(
                side=side,
                entry=entry,
                stop=stop,
                target=objective.price,
                tick_size=tick_size,
                entry_style=order_type,
            )
            if not economics:
                continue
            # Do not create economically impossible geometries.  This is not a
            # performance threshold: a target that pays no positive net R can
            # never have positive expectancy at any probability.
            if economics["target_net_r"] <= 0.0 or economics["stop_net_r"] >= 0.0:
                continue
            key = (decision_stage, entry_name, objective.objective_id)
            if key in context.emitted:
                continue
            context.emitted.add(key)
            action_id = (
                f"AEP:{context.episode_id}:{decision_stage}:{entry_name}:"
                f"T{objective_rank}:{_stable_id(objective.objective_id)}"
            )
            values = {
                **context.approach,
                **feature_values,
                **economics,
                **_bar_features(row, "decision"),
                "event_extreme_bps": abs(event_extreme - context.boundary.price) / max(abs(context.boundary.price), 1e-12) * 10_000.0,
                "decision_progress_bps": _signed_progress(side, context.boundary.price, decision_close) / max(abs(context.boundary.price), 1e-12) * 10_000.0,
                "entry_distance_from_decision_bps": abs(decision_close - entry) / max(abs(entry), 1e-12) * 10_000.0,
                "objective_rank": float(objective_rank),
                "entry_order_type": order_type,
                "causal_structure_policy": CAUSAL_STRUCTURE_POLICY,
                "action_policy": ACTION_POLICY,
                "limit_fill_policy": LIMIT_FILL_POLICY,
            }
            actions.append(
                ActionSpec(
                    action_id=action_id,
                    episode_id=context.episode_id,
                    symbol=context.boundary.symbol,
                    event_type=event_type,
                    decision_stage=decision_stage,
                    side=side,
                    emission_index=emission_index,
                    emission_time_ns=emission_time_ns,
                    entry_style=entry_name,
                    entry=float(entry),
                    stop=float(stop),
                    target=float(objective.price),
                    entry_expiry_minutes=expiry,
                    source_level_id=context.boundary.level_id,
                    source_kind=context.boundary.source_kind,
                    source_timeframe_minutes=context.boundary.timeframe_minutes,
                    source_span=context.boundary.span,
                    source_price=context.boundary.price,
                    source_lower=context.boundary.lower,
                    source_upper=context.boundary.upper,
                    source_strength_ratio=context.boundary.strength_ratio,
                    source_defense_count=context.boundary.defense_count,
                    source_age_minutes=(emission_time_ns - context.boundary.observed_time_ns) / NS_PER_MINUTE,
                    objective_id=objective.objective_id,
                    objective_kind=objective.kind,
                    objective_timeframe_minutes=objective.timeframe_minutes,
                    objective_strength_ratio=objective.strength_ratio,
                    interaction_time_ns=context.interaction_time_ns,
                    feature_values=values,
                ),
            )
    return actions


def _failed_auction_actions(
    data: pd.DataFrame,
    levels: Sequence[PivotLevel],
    context: EpisodeContext,
    tick_size: float,
    horizon_bars: int = 30,
) -> list[ActionSpec]:
    boundary = context.boundary
    side = _source_reversal_side(boundary)
    interaction = context.interaction_index
    end = min(len(data), interaction + horizon_bars + 1)
    before = data.iloc[max(0, interaction - 5) : interaction]
    micro_reference = (
        float(before["high"].max()) if side == "LONG" and not before.empty
        else float(before["low"].min()) if side == "SHORT" and not before.empty
        else None
    )
    extreme = context.interaction_extreme
    first_penetration: int | None = None
    reclaim_index: int | None = None
    detach_index: int | None = None
    cumulative_signed_quote = 0.0
    cumulative_quote = 0.0
    output: list[ActionSpec] = []

    for index in range(interaction, end):
        row = data.iloc[index]
        if side == "LONG":
            penetrated = float(row["low"]) < boundary.lower
            extreme = min(extreme, float(row["low"]))
            reclaimed = penetrated and float(row["close"]) >= boundary.upper
        else:
            penetrated = float(row["high"]) > boundary.upper
            extreme = max(extreme, float(row["high"]))
            reclaimed = penetrated and float(row["close"]) <= boundary.lower
        if penetrated and first_penetration is None:
            first_penetration = index
        cumulative_signed_quote += _side_sign(side) * _finite(row.get("signed_quote"), 0.0)
        cumulative_quote += _finite(row.get("quote_volume"), 0.0)

        if first_penetration is not None and reclaim_index is None:
            if side == "LONG":
                reclaimed = float(row["close"]) >= boundary.upper
            else:
                reclaimed = float(row["close"]) <= boundary.lower
            if reclaimed and index - first_penetration <= 6:
                reclaim_index = index
            elif index - first_penetration > 6:
                break
        if reclaim_index is None:
            continue

        # A local control transfer is deliberately required before the episode
        # owns a reversal action.  Merely closing back inside is not enough.
        if detach_index is None and micro_reference is not None:
            if side == "LONG":
                detached = float(row["close"]) > micro_reference and float(row["close"]) > float(row["open"])
            else:
                detached = float(row["close"]) < micro_reference and float(row["close"]) < float(row["open"])
            if detached:
                detach_index = index
                features = {
                    "penetration_to_reclaim_minutes": float(reclaim_index - first_penetration),
                    "reclaim_to_decision_minutes": float(index - reclaim_index),
                    "episode_cumulative_aligned_delta_share": cumulative_signed_quote / max(cumulative_quote, 1e-12),
                    "reclaim_close_distance_bps": abs(float(data.iloc[reclaim_index]["close"]) - boundary.price) / max(abs(boundary.price), 1e-12) * 10_000.0,
                    **_bar_features(data.iloc[reclaim_index], "reclaim"),
                }
                output.extend(
                    _make_actions(
                        data=data,
                        levels=levels,
                        context=context,
                        event_type="FAILED_AUCTION",
                        decision_stage="RECLAIM_CONTROL_TRANSFER",
                        side=side,
                        emission_index=index,
                        event_extreme=extreme,
                        stop_reference=extreme,
                        micro_reference=micro_reference,
                        feature_values=features,
                        tick_size=tick_size,
                    ),
                )
                continue

        if detach_index is not None and index > detach_index:
            invalidated = float(row["low"]) <= extreme if side == "LONG" else float(row["high"]) >= extreme
            if invalidated:
                break
            retested = (
                float(row["low"]) <= boundary.upper + tick_size
                if side == "LONG"
                else float(row["high"]) >= boundary.lower - tick_size
            )
            response = (
                retested and float(row["close"]) > float(row["open"]) and float(row["close"]) >= boundary.upper
                if side == "LONG"
                else retested and float(row["close"]) < float(row["open"]) and float(row["close"]) <= boundary.lower
            )
            if response:
                prior_row = data.iloc[index - 1]
                control = (
                    float(row["close"]) > float(prior_row["high"])
                    if side == "LONG"
                    else float(row["close"]) < float(prior_row["low"])
                )
                if control:
                    features = {
                        "penetration_to_reclaim_minutes": float(reclaim_index - first_penetration),
                        "reclaim_to_decision_minutes": float(index - reclaim_index),
                        "detach_to_retest_minutes": float(index - detach_index),
                        "episode_cumulative_aligned_delta_share": cumulative_signed_quote / max(cumulative_quote, 1e-12),
                        **_bar_features(data.iloc[reclaim_index], "reclaim"),
                        **_bar_features(row, "retest_response"),
                    }
                    output.extend(
                        _make_actions(
                            data=data,
                            levels=levels,
                            context=context,
                            event_type="FAILED_AUCTION",
                            decision_stage="FIRST_RETEST_RESPONSE",
                            side=side,
                            emission_index=index,
                            event_extreme=extreme,
                            stop_reference=extreme,
                            micro_reference=micro_reference,
                            feature_values=features,
                            tick_size=tick_size,
                        ),
                    )
                    break
    return output


def _accepted_auction_actions(
    data: pd.DataFrame,
    levels: Sequence[PivotLevel],
    context: EpisodeContext,
    tick_size: float,
    horizon_bars: int = 35,
) -> list[ActionSpec]:
    boundary = context.boundary
    side = _source_continuation_side(boundary)
    interaction = context.interaction_index
    end = min(len(data), interaction + horizon_bars + 1)
    before = data.iloc[max(0, interaction - 8) : interaction]
    break_origin = (
        float(before["high"].max()) if side == "SHORT" and not before.empty
        else float(before["low"].min()) if side == "LONG" and not before.empty
        else context.interaction_extreme
    )
    micro_reference = (
        float(before["low"].min()) if side == "SHORT" and not before.empty
        else float(before["high"].max()) if side == "LONG" and not before.empty
        else None
    )
    first_outside: int | None = None
    hold_index: int | None = None
    output: list[ActionSpec] = []
    cumulative_signed_quote = 0.0
    cumulative_quote = 0.0
    event_extreme = context.interaction_extreme

    for index in range(interaction, end):
        row = data.iloc[index]
        cumulative_signed_quote += _side_sign(side) * _finite(row.get("signed_quote"), 0.0)
        cumulative_quote += _finite(row.get("quote_volume"), 0.0)
        if side == "LONG":
            outside = float(row["close"]) > boundary.upper
            event_extreme = max(event_extreme, float(row["high"]))
            reclaimed_inside = float(row["close"]) < boundary.lower
        else:
            outside = float(row["close"]) < boundary.lower
            event_extreme = min(event_extreme, float(row["low"]))
            reclaimed_inside = float(row["close"]) > boundary.upper

        if first_outside is None:
            if outside:
                first_outside = index
            continue
        if reclaimed_inside and hold_index is None:
            break
        if hold_index is None:
            previous = data.iloc[index - 1]
            previous_outside = (
                float(previous["close"]) > boundary.upper
                if side == "LONG"
                else float(previous["close"]) < boundary.lower
            )
            aligned_body = (
                float(row["close"]) > float(row["open"])
                if side == "LONG"
                else float(row["close"]) < float(row["open"])
            )
            if outside and previous_outside and aligned_body:
                hold_index = index
                features = {
                    "break_to_hold_minutes": float(index - first_outside),
                    "episode_cumulative_aligned_delta_share": cumulative_signed_quote / max(cumulative_quote, 1e-12),
                    **_bar_features(data.iloc[first_outside], "break"),
                    **_bar_features(row, "hold"),
                }
                output.extend(
                    _make_actions(
                        data=data,
                        levels=levels,
                        context=context,
                        event_type="ACCEPTED_AUCTION",
                        decision_stage="BREAK_HOLD",
                        side=side,
                        emission_index=index,
                        event_extreme=event_extreme,
                        stop_reference=break_origin,
                        micro_reference=micro_reference,
                        feature_values=features,
                        tick_size=tick_size,
                    ),
                )
                continue

        if hold_index is not None and index > hold_index:
            # The first return to the transferred boundary must close on the new
            # side and show a one-bar response.  A close back through the old
            # auction invalidates continuation immediately.
            if reclaimed_inside:
                break
            retested = (
                float(row["low"]) <= boundary.upper + tick_size
                if side == "LONG"
                else float(row["high"]) >= boundary.lower - tick_size
            )
            if not retested:
                continue
            aligned_body = (
                float(row["close"]) > float(row["open"]) and float(row["close"]) >= boundary.upper
                if side == "LONG"
                else float(row["close"]) < float(row["open"]) and float(row["close"]) <= boundary.lower
            )
            prior = data.iloc[index - 1]
            control = (
                float(row["close"]) > float(prior["high"])
                if side == "LONG"
                else float(row["close"]) < float(prior["low"])
            )
            if aligned_body and control:
                retest_extreme = float(row["low"]) if side == "LONG" else float(row["high"])
                features = {
                    "break_to_hold_minutes": float(hold_index - first_outside),
                    "hold_to_retest_minutes": float(index - hold_index),
                    "episode_cumulative_aligned_delta_share": cumulative_signed_quote / max(cumulative_quote, 1e-12),
                    **_bar_features(data.iloc[first_outside], "break"),
                    **_bar_features(data.iloc[hold_index], "hold"),
                    **_bar_features(row, "retest_response"),
                }
                output.extend(
                    _make_actions(
                        data=data,
                        levels=levels,
                        context=context,
                        event_type="ACCEPTED_AUCTION",
                        decision_stage="FIRST_RETEST_RESPONSE",
                        side=side,
                        emission_index=index,
                        event_extreme=event_extreme,
                        stop_reference=retest_extreme,
                        micro_reference=micro_reference,
                        feature_values=features,
                        tick_size=tick_size,
                    ),
                )
                break
    return output


def label_action(data: pd.DataFrame, action: ActionSpec, tick_size: float) -> LabelResult:
    sign = _side_sign(action.side)
    risk = abs(action.entry - action.stop)
    economics = _economics(
        side=action.side,
        entry=action.entry,
        stop=action.stop,
        target=action.target,
        tick_size=tick_size,
        entry_style="MARKET" if action.entry_style == "MARKET" else "LIMIT",
    )
    target_net = economics["target_net_r"]
    stop_net = economics["stop_net_r"]
    start = action.emission_index + 1
    if start >= len(data):
        return LabelResult("NO_FUTURE", "UNRESOLVED", None, None, None, None, None, None, target_net, stop_net, None, None, None)

    if action.entry_style == "MARKET":
        fill_index = start
        fill_state = "FILLED_MARKET"
    else:
        expiry = min(len(data) - 1, action.emission_index + action.entry_expiry_minutes)
        fill_index = None
        for index in range(start, expiry + 1):
            row = data.iloc[index]
            traded_through = (
                float(row["low"]) <= action.entry - LIMIT_TRADE_THROUGH_TICKS * tick_size
                if action.side == "LONG"
                else float(row["high"]) >= action.entry + LIMIT_TRADE_THROUGH_TICKS * tick_size
            )
            if traded_through:
                fill_index = index
                break
            invalidated = float(row["low"]) <= action.stop if action.side == "LONG" else float(row["high"]) >= action.stop
            target_spent = float(row["high"]) >= action.target if action.side == "LONG" else float(row["low"]) <= action.target
            if invalidated or target_spent:
                return LabelResult(
                    "CANCELED_PRE_FILL_INVALIDATION" if invalidated else "CANCELED_PRE_FILL_TARGET_SPENT",
                    "UNFILLED",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    target_net,
                    stop_net,
                    None,
                    None,
                    None,
                )
        if fill_index is None:
            return LabelResult("EXPIRED_UNFILLED", "UNFILLED", None, None, None, None, None, None, target_net, stop_net, None, None, None)
        fill_state = "FILLED_LIMIT"

    fill_time_ns = _time_ns(data.index, fill_index)
    fill_bar = data.iloc[fill_index]
    stop_on_fill = (
        float(fill_bar["low"]) <= action.stop
        if action.side == "LONG"
        else float(fill_bar["high"]) >= action.stop
    )
    target_on_fill = (
        float(fill_bar["high"]) >= action.target
        if action.side == "LONG"
        else float(fill_bar["low"]) <= action.target
    )
    if stop_on_fill:
        return LabelResult(
            fill_state,
            "STOP_FIRST",
            fill_index,
            fill_time_ns,
            fill_index,
            fill_time_ns,
            float(fill_index - action.emission_index),
            0.0,
            target_net,
            stop_net,
            stop_net,
            0.0,
            min(0.0, sign * (float(fill_bar["low"] if action.side == "LONG" else fill_bar["high"]) - action.entry) / risk),
        )
    if target_on_fill:
        # A one-minute bar does not reveal whether the maker fill occurred before
        # or after the target print.  Never credit this as a win or allow a later
        # target revisit to resolve it; keep it outside the resolved training set.
        return LabelResult(
            fill_state,
            "AMBIGUOUS_FILL_TARGET_SAME_MINUTE",
            fill_index,
            fill_time_ns,
            fill_index,
            fill_time_ns,
            float(fill_index - action.emission_index),
            0.0,
            target_net,
            stop_net,
            None,
            max(0.0, sign * (float(fill_bar["high"] if action.side == "LONG" else fill_bar["low"]) - action.entry) / risk),
            0.0,
        )

    best_favorable = 0.0
    worst_adverse = 0.0
    for index in range(fill_index + 1, len(data)):
        row = data.iloc[index]
        if action.side == "LONG":
            target_hit = float(row["high"]) >= action.target
            stop_hit = float(row["low"]) <= action.stop
            favorable = (float(row["high"]) - action.entry) / risk
            adverse = (float(row["low"]) - action.entry) / risk
        else:
            target_hit = float(row["low"]) <= action.target
            stop_hit = float(row["high"]) >= action.stop
            favorable = (action.entry - float(row["low"])) / risk
            adverse = (action.entry - float(row["high"])) / risk
        best_favorable = max(best_favorable, favorable)
        worst_adverse = min(worst_adverse, adverse)
        if target_hit and stop_hit:
            outcome = "AMBIGUOUS_SAME_MINUTE"
            net_r = stop_net
        elif stop_hit:
            outcome = "STOP_FIRST"
            net_r = stop_net
        elif target_hit:
            outcome = "TARGET_FIRST"
            net_r = target_net
        else:
            continue
        resolution_time_ns = _time_ns(data.index, index)
        return LabelResult(
            fill_state,
            outcome,
            fill_index,
            fill_time_ns,
            index,
            resolution_time_ns,
            float(fill_index - action.emission_index),
            float(index - fill_index),
            target_net,
            stop_net,
            net_r,
            best_favorable,
            worst_adverse,
        )
    return LabelResult(
        fill_state,
        "UNRESOLVED",
        fill_index,
        fill_time_ns,
        None,
        None,
        float(fill_index - action.emission_index),
        None,
        target_net,
        stop_net,
        None,
        best_favorable,
        worst_adverse,
    )


def generate_actions_for_symbol(
    symbol: str,
    raw: pd.DataFrame,
    *,
    trading_start: date,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    tick_size = CONTRACTS[symbol].tick_size
    data = prepare_one_minute(raw, tick_size)
    aggregates = {minutes: _resample_flow(raw, minutes) for minutes in OBJECTIVE_TIMEFRAMES}
    levels = detect_pivots(symbol, data, aggregates, tick_size)
    source_levels = [item for item in levels if item.timeframe_minutes in SOURCE_TIMEFRAMES]
    source_levels.sort(key=lambda item: (item.observed_index_1m, item.level_id))
    available: list[PivotLevel] = []
    next_source = 0
    actions: list[ActionSpec] = []
    episode_count = 0
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)

    for index, (timestamp, row) in enumerate(data.iterrows()):
        now_ns = int(timestamp.value)
        while next_source < len(source_levels) and source_levels[next_source].observed_index_1m < index:
            available.append(source_levels[next_source])
            next_source += 1
        if now_ns < start_ns or not available:
            continue
        touched = [
            level
            for level in available
            if not level.retired_as_source
            and float(row["low"]) <= level.upper
            and float(row["high"]) >= level.lower
        ]
        if not touched:
            continue
        # One support and one resistance episode may coexist on an unusually
        # wide bar.  Within each side, the larger/stronger pre-existing auction
        # owns the interaction and nearby duplicate levels are retired together.
        for source_side in ("LOW", "HIGH"):
            candidates = [item for item in touched if item.side == source_side]
            if not candidates:
                continue
            boundary = max(
                candidates,
                key=lambda item: (
                    item.timeframe_minutes,
                    item.defense_count,
                    item.strength_ratio,
                    -abs(item.price - float(row["close"])),
                ),
            )
            width = max(boundary.upper - boundary.lower, 4.0 * tick_size)
            for item in available:
                if (
                    item.side == source_side
                    and not item.retired_as_source
                    and abs(item.price - boundary.price) <= 1.5 * width
                ):
                    item.retired_as_source = True
            episode_count += 1
            episode_id = (
                f"AEP:{symbol}:{int(timestamp.value)}:{source_side}:"
                f"{_stable_id(boundary.level_id)}"
            )
            extreme = float(row["low"] if source_side == "LOW" else row["high"])
            context = EpisodeContext(
                episode_id=episode_id,
                boundary=boundary,
                interaction_index=index,
                interaction_time_ns=now_ns,
                interaction_extreme=extreme,
                approach=_approach_features(data, index, boundary),
            )
            actions.extend(_failed_auction_actions(data, levels, context, tick_size))
            actions.extend(_accepted_auction_actions(data, levels, context, tick_size))

    records: list[dict[str, Any]] = []
    for action in actions:
        label = label_action(data, action, tick_size)
        record = {
            **{key: value for key, value in asdict(action).items() if key != "feature_values"},
            **action.feature_values,
            **asdict(label),
        }
        records.append(record)
    frame = pd.DataFrame(records)
    if not frame.empty and frame["action_id"].duplicated().any():
        raise RuntimeError(f"duplicate action ids for {symbol}")
    summary = {
        "symbol": symbol,
        "one_minute_bars": int(len(data)),
        "pivot_levels": int(len(levels)),
        "source_levels": int(len(source_levels)),
        "episodes": int(episode_count),
        "actions": int(len(frame)),
        "fills": int(frame["fill_state"].astype(str).str.startswith("FILLED").sum()) if not frame.empty else 0,
        "outcomes": (
            {str(key): int(value) for key, value in frame["outcome"].value_counts().items()}
            if not frame.empty
            else {}
        ),
        "policies": [CAUSAL_STRUCTURE_POLICY, ACTION_POLICY, LIMIT_FILL_POLICY],
    }
    return frame, summary


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
    frames: list[pd.DataFrame] = []
    summaries: dict[str, Any] = {}
    load_start = start - timedelta(days=warmup_days)
    for symbol in symbols:
        if symbol not in CONTRACTS:
            raise ValueError(f"unsupported symbol {symbol}")
        raw = load_range_flow(symbol, load_start, end, cache)
        frame, summary = generate_actions_for_symbol(symbol, raw, trading_start=start)
        if not frame.empty:
            frames.append(frame)
            frame.to_csv(output / f"{symbol}_auction_actions.csv", index=False)
        summaries[symbol] = summary
    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    combined.to_csv(output / "auction_actions.csv", index=False)
    resolved = combined[combined["outcome"].isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"])] if not combined.empty else combined
    filled = combined[combined["fill_state"].astype(str).str.startswith("FILLED")] if not combined.empty else combined
    summary: dict[str, Any] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "warmup_days": warmup_days,
        "symbols": list(symbols),
        "actions": int(len(combined)),
        "filled_actions": int(len(filled)),
        "resolved_actions": int(len(resolved)),
        "target_first": int((resolved["outcome"] == "TARGET_FIRST").sum()) if not resolved.empty else 0,
        "stop_first_or_ambiguous": int((resolved["outcome"] != "TARGET_FIRST").sum()) if not resolved.empty else 0,
        "mean_net_r_resolved": _finite(resolved["net_r"].mean(), 0.0) if not resolved.empty else None,
        "by_symbol": summaries,
        "causal_structure_policy": CAUSAL_STRUCTURE_POLICY,
        "action_policy": ACTION_POLICY,
        "limit_fill_policy": LIMIT_FILL_POLICY,
        "future_information_in_features": False,
        "future_information_in_labels_only": True,
    }
    (output / "auction_actions_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


__all__ = [
    "ActionSpec",
    "LabelResult",
    "generate_actions_for_symbol",
    "label_action",
    "prepare_one_minute",
    "run_research",
]
