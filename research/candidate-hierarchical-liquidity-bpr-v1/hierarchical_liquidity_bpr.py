"""Hierarchical external-liquidity ledger with PO3/BPR first-return execution.

The strategy is a structural replacement, not a filter on legacy plans.

1. Confirm external liquidity causally on 15m, 60m, 4h, 12h, daily and weekly scales.
2. Observe the *first* acquisition of one side of that liquidity.
3. Require a quick manipulation/reclaim, then either:
   - an opposite-FVG overlap (BPR),
   - inversion of the manipulation FVG (IFVG), or
   - a causal market-structure shift that leaves a fresh FVG.
4. Wait for departure and the first controlled return to that balanced/inefficient zone.
5. Enter only after a completed price/flow response, on the next one-minute open.
6. Invalidate at the manipulation extreme.
7. Target the nearest still-unconsumed external liquidity of matching or larger scale.
8. A confirmed directional ledger suppresses lower-scale countertrend reversals before
   its matched target is reached.

The same logic and normalized state are used for BTCUSDT, ETHUSDT, SOLUSDT and
XRPUSDT. Future bars are used only by the conservative first-passage labeler.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence
import hashlib
import json
import math

import numpy as np
import pandas as pd

from auction_episode_research import (
    CONTRACTS,
    ActionSpec,
    _economics,
    _resample_flow,
    _stable_id,
    _time_ns,
    label_action,
)
from derivatives_dislocation import prepare_market_state
from liquidity_displacement import _add_common_state
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics


POLICY = (
    "HIERARCHICAL_LIQUIDITY_LEDGER:EXTERNAL_LIQUIDITY_FIRST_ACQUISITION_THEN_"
    "PO3_MANIPULATION_RECLAIM_THEN_BPR_OR_IFVG_OR_MSS_FVG_THEN_DEPARTURE_"
    "FIRST_RETURN_COMPLETED_RESPONSE_THEN_NEXT_MINUTE_ENTRY_TO_SCALE_MATCHED_"
    "UNCONSUMED_EXTERNAL_LIQUIDITY"
)
PIVOT_SPANS: dict[int, tuple[int, ...]] = {
    15: (2, 4),
    60: (2,),
    240: (1, 2),
    720: (1,),
    1440: (1,),
}
PERIOD_EXTREMES = (1440, 10080)
MAX_RECLAIM_MINUTES = 10
MAX_CONFIRM_MINUTES = 18
MAX_DEPARTURE_MINUTES = 10
MAX_RETURN_MINUTES = 35
MAX_RESPONSE_BARS = 3
MAX_HOLD_MINUTES = 360
EPS = 1e-12


@dataclass(slots=True)
class LiquidityLevel:
    level_id: str
    symbol: str
    side: str
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
    source_kind: str
    first_penetration_index: int | None = None


@dataclass(frozen=True, slots=True)
class Gap:
    side: str
    observed_index: int
    lower: float
    upper: float
    middle_body_ratio: float
    middle_range_ratio: float
    middle_activity_ratio: float
    middle_delta_signed: float


@dataclass(frozen=True, slots=True)
class Setup:
    setup_kind: str
    side: str
    interaction_index: int
    reclaim_index: int
    event_extreme: float
    confirmation_index: int
    lower: float
    upper: float
    manipulation_gap: Gap | None
    directional_gap: Gap
    pre_event_control: float


@dataclass(slots=True)
class Ledger:
    side: str
    source_timeframe_minutes: int
    source_price: float
    target_price: float
    invalidation_price: float
    emission_index: int
    target_level_id: str


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _first_penetration(data: pd.DataFrame, level: LiquidityLevel) -> int | None:
    start = min(len(data), level.observed_index_1m + 1)
    if start >= len(data):
        return None
    if level.side == "HIGH":
        mask = data["high"].iloc[start:].to_numpy(float) > level.upper
    else:
        mask = data["low"].iloc[start:].to_numpy(float) < level.lower
    indices = np.flatnonzero(mask)
    return start + int(indices[0]) if len(indices) else None


def _detect_pivot_levels(
    symbol: str,
    one_minute: pd.DataFrame,
    aggregates: dict[int, pd.DataFrame],
    tick_size: float,
) -> list[LiquidityLevel]:
    output: list[LiquidityLevel] = []
    histories: dict[tuple[int, str], list[LiquidityLevel]] = {}
    one_index = one_minute.index
    for timeframe, spans in PIVOT_SPANS.items():
        bars = aggregates[timeframe]
        ranges = (bars["high"] - bars["low"]).astype(float)
        prior_atr = ranges.shift(1).rolling(20, min_periods=5).median()
        highs = bars["high"].to_numpy(float)
        lows = bars["low"].to_numpy(float)
        for span in spans:
            for center in range(span, len(bars) - span):
                observed_position = center + span
                observed_time = bars.index[observed_position]
                observed_1m = int(one_index.searchsorted(observed_time, side="left"))
                if observed_1m >= len(one_index):
                    continue
                atr = _finite(prior_atr.iloc[observed_position], max(tick_size, ranges.iloc[center]))
                width = max(2.0 * tick_size, 0.06 * atr)
                high_window = highs[center - span:center + span + 1]
                low_window = lows[center - span:center + span + 1]
                for side in ("HIGH", "LOW"):
                    if side == "HIGH":
                        unique = highs[center] == np.nanmax(high_window) and int(np.sum(high_window == highs[center])) == 1
                        if not unique:
                            continue
                        price = float(highs[center])
                        prominence = min(price - float(np.nanmin(low_window[:span])), price - float(np.nanmin(low_window[span + 1:])))
                    else:
                        unique = lows[center] == np.nanmin(low_window) and int(np.sum(low_window == lows[center])) == 1
                        if not unique:
                            continue
                        price = float(lows[center])
                        prominence = min(float(np.nanmax(high_window[:span])) - price, float(np.nanmax(high_window[span + 1:])) - price)
                    strength = prominence / max(atr, tick_size)
                    key = (timeframe, side)
                    prior = histories.setdefault(key, [])
                    tolerance = max(4.0 * tick_size, 0.18 * atr)
                    defense = 1 + sum(
                        1 for item in prior[-24:]
                        if item.observed_time_ns < int(observed_time.value) and abs(item.price - price) <= tolerance
                    )
                    level = LiquidityLevel(
                        level_id=f"{symbol}:{timeframe}m:{side}:c{center}:s{span}:{int(observed_time.value)}",
                        symbol=symbol,
                        side=side,
                        timeframe_minutes=timeframe,
                        span=span,
                        price=price,
                        lower=price - width,
                        upper=price + width,
                        event_time_ns=int(bars.index[center].value),
                        observed_time_ns=int(observed_time.value),
                        observed_index_1m=observed_1m,
                        strength_ratio=float(strength),
                        defense_count=int(defense),
                        source_kind=f"{timeframe}M_CONFIRMED_EXTERNAL_{side}",
                    )
                    prior.append(level)
                    output.append(level)
    return output


def _detect_period_extremes(
    symbol: str,
    one_minute: pd.DataFrame,
    aggregates: dict[int, pd.DataFrame],
    tick_size: float,
) -> list[LiquidityLevel]:
    output: list[LiquidityLevel] = []
    one_index = one_minute.index
    for timeframe in PERIOD_EXTREMES:
        bars = aggregates[timeframe]
        ranges = (bars["high"] - bars["low"]).astype(float)
        for position in range(len(bars)):
            observed_time = bars.index[position]
            observed_1m = int(one_index.searchsorted(observed_time, side="left"))
            if observed_1m >= len(one_index):
                continue
            width = max(2.0 * tick_size, 0.03 * _finite(ranges.iloc[position], tick_size))
            for side, column in (("HIGH", "high"), ("LOW", "low")):
                price = float(bars.iloc[position][column])
                output.append(
                    LiquidityLevel(
                        level_id=f"{symbol}:{timeframe}m:COMPLETED_PERIOD_{side}:{int(observed_time.value)}",
                        symbol=symbol,
                        side=side,
                        timeframe_minutes=timeframe,
                        span=0,
                        price=price,
                        lower=price - width,
                        upper=price + width,
                        event_time_ns=int(observed_time.value - timeframe * 60_000_000_000),
                        observed_time_ns=int(observed_time.value),
                        observed_index_1m=observed_1m,
                        strength_ratio=1.0,
                        defense_count=1,
                        source_kind=f"PREVIOUS_{'DAY' if timeframe == 1440 else 'WEEK'}_{side}",
                    )
                )
    return output


def detect_hierarchical_liquidity(
    symbol: str,
    one_minute: pd.DataFrame,
    raw: pd.DataFrame,
    tick_size: float,
) -> list[LiquidityLevel]:
    timeframes = sorted(set(PIVOT_SPANS) | set(PERIOD_EXTREMES))
    aggregates = {minutes: _resample_flow(raw, minutes) for minutes in timeframes}
    levels = _detect_pivot_levels(symbol, one_minute, aggregates, tick_size)
    levels.extend(_detect_period_extremes(symbol, one_minute, aggregates, tick_size))
    levels.sort(key=lambda item: (item.observed_index_1m, item.timeframe_minutes, item.side, item.level_id))
    for level in levels:
        level.first_penetration_index = _first_penetration(one_minute, level)
    return levels


def _gap_at(data: pd.DataFrame, index: int, tick: float) -> Gap | None:
    if index < 2:
        return None
    first = data.iloc[index - 2]
    middle = data.iloc[index - 1]
    third = data.iloc[index]
    middle_body_ratio = _finite(middle.get("body_ratio"), 0.0)
    middle_range_ratio = _finite(middle.get("range_ratio"), 0.0)
    if middle_body_ratio < 1.15 or middle_range_ratio < 1.0:
        return None
    if float(third["low"]) > float(first["high"]) + tick:
        side = "LONG"
        lower, upper = float(first["high"]), float(third["low"])
    elif float(third["high"]) < float(first["low"]) - tick:
        side = "SHORT"
        lower, upper = float(third["high"]), float(first["low"])
    else:
        return None
    return Gap(
        side=side,
        observed_index=index,
        lower=lower,
        upper=upper,
        middle_body_ratio=middle_body_ratio,
        middle_range_ratio=middle_range_ratio,
        middle_activity_ratio=_finite(middle.get("activity_ratio"), 0.0),
        middle_delta_signed=_sign(side) * _finite(middle.get("delta_share"), 0.0),
    )


def _detect_manipulation(data: pd.DataFrame, interaction: int, level: LiquidityLevel) -> tuple[int, float] | None:
    source_low = level.side == "LOW"
    extreme = float(data.iloc[interaction]["low"] if source_low else data.iloc[interaction]["high"])
    for index in range(interaction, min(len(data), interaction + MAX_RECLAIM_MINUTES + 1)):
        row = data.iloc[index]
        extreme = min(extreme, float(row["low"])) if source_low else max(extreme, float(row["high"]))
        reclaimed = float(row["close"]) >= level.upper if source_low else float(row["close"]) <= level.lower
        if reclaimed:
            return index, extreme
    return None


def _balance_features(data: pd.DataFrame, interaction: int) -> dict[str, float]:
    frame = data.iloc[max(0, interaction - 120):interaction]
    if len(frame) < 20:
        return {}
    output: dict[str, float] = {}
    for minutes in (30, 60, 120):
        window = frame.iloc[-minutes:]
        if len(window) < max(15, minutes // 2):
            continue
        log_close = np.log(window["close"].astype(float).clip(lower=EPS))
        returns = log_close.diff().dropna()
        net = abs(float(log_close.iloc[-1] - log_close.iloc[0]))
        path = float(returns.abs().sum())
        output[f"pre_balance_efficiency_{minutes}m"] = net / max(path, EPS)
        output[f"pre_balance_range_bps_{minutes}m"] = (float(window.high.max()) - float(window.low.min())) / max(abs(float(window.close.iloc[-1])), EPS) * 10_000.0
        output[f"pre_balance_turn_rate_{minutes}m"] = float((np.sign(returns).diff().fillna(0.0) != 0.0).mean())
        output[f"pre_balance_activity_ratio_{minutes}m"] = _finite(window.activity_ratio.median(), 0.0)
    return output


def _pre_event_control(data: pd.DataFrame, interaction: int, side: str) -> float:
    before = data.iloc[max(0, interaction - 20):interaction]
    if before.empty:
        return float(data.iloc[interaction]["close"])
    return float(before.high.max()) if side == "LONG" else float(before.low.min())


def _detect_setup(
    data: pd.DataFrame,
    interaction: int,
    reclaim: int,
    extreme: float,
    level: LiquidityLevel,
    tick: float,
) -> Setup | None:
    side = "LONG" if level.side == "LOW" else "SHORT"
    opposite = "SHORT" if side == "LONG" else "LONG"
    manipulation_gaps = [gap for index in range(max(2, interaction - 2), reclaim + 1) if (gap := _gap_at(data, index, tick)) is not None and gap.side == opposite]
    control = _pre_event_control(data, interaction, side)
    directional_gaps: list[Gap] = []
    candidates: list[tuple[int, int, Setup]] = []
    for index in range(max(2, reclaim), min(len(data), reclaim + MAX_CONFIRM_MINUTES + 1)):
        row = data.iloc[index]
        gap = _gap_at(data, index, tick)
        if gap is not None and gap.side == side:
            directional_gaps.append(gap)
            for manipulation_gap in reversed(manipulation_gaps):
                lower = max(gap.lower, manipulation_gap.lower)
                upper = min(gap.upper, manipulation_gap.upper)
                if upper > lower + tick:
                    candidates.append((index, 0, Setup("BPR", side, interaction, reclaim, extreme, index, lower, upper, manipulation_gap, gap, control)))
                    break
        for manipulation_gap in reversed(manipulation_gaps):
            inverted = float(row["close"]) > manipulation_gap.upper + tick if side == "LONG" else float(row["close"]) < manipulation_gap.lower - tick
            if inverted:
                directional = gap if gap is not None and gap.side == side else Gap(
                    side=side,
                    observed_index=index,
                    lower=manipulation_gap.lower,
                    upper=manipulation_gap.upper,
                    middle_body_ratio=_finite(row.get("body_ratio"), 0.0),
                    middle_range_ratio=_finite(row.get("range_ratio"), 0.0),
                    middle_activity_ratio=_finite(row.get("activity_ratio"), 0.0),
                    middle_delta_signed=_sign(side) * _finite(row.get("delta_share"), 0.0),
                )
                candidates.append((index, 1, Setup("IFVG", side, interaction, reclaim, extreme, index, manipulation_gap.lower, manipulation_gap.upper, manipulation_gap, directional, control)))
                break
        shifted = float(row["close"]) > control + tick if side == "LONG" else float(row["close"]) < control - tick
        if shifted and directional_gaps:
            directional_gap = directional_gaps[-1]
            candidates.append((index, 2, Setup("MSS_FVG", side, interaction, reclaim, extreme, index, directional_gap.lower, directional_gap.upper, manipulation_gaps[-1] if manipulation_gaps else None, directional_gap, control)))
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            return candidates[0][2]
    return None


def _first_return_response(data: pd.DataFrame, setup: Setup, tick: float) -> dict[str, Any] | None:
    side = setup.side
    departure: int | None = None
    for index in range(setup.confirmation_index + 1, min(len(data), setup.confirmation_index + MAX_DEPARTURE_MINUTES + 1)):
        close = float(data.iloc[index]["close"])
        away = close > setup.upper + tick if side == "LONG" else close < setup.lower - tick
        if away:
            departure = index
            break
    if departure is None:
        return None
    touch: int | None = None
    retest_extreme: float | None = None
    for index in range(departure + 1, min(len(data), departure + MAX_RETURN_MINUTES + 1)):
        row = data.iloc[index]
        overlaps = float(row["low"]) <= setup.upper and float(row["high"]) >= setup.lower
        if touch is None:
            if not overlaps:
                continue
            touch = index
            retest_extreme = float(row["low"] if side == "LONG" else row["high"])
        else:
            retest_extreme = min(float(retest_extreme), float(row["low"])) if side == "LONG" else max(float(retest_extreme), float(row["high"]))
        if index - touch > MAX_RESPONSE_BARS:
            return None
        spent = float(row["close"]) < setup.lower - tick if side == "LONG" else float(row["close"]) > setup.upper + tick
        if spent:
            return None
        prior = data.iloc[index - 1]
        aligned_body = float(row["close"] - row["open"]) * _sign(side) > 0.0
        closes_away = float(row["close"]) >= setup.upper if side == "LONG" else float(row["close"]) <= setup.lower
        local_control = float(row["close"]) > float(prior["high"]) if side == "LONG" else float(row["close"]) < float(prior["low"])
        delta = _sign(side) * _finite(row.get("delta_share"), 0.0)
        price_progress = _sign(side) * float(row["close"] - row["open"])
        initiative = delta > 0.0
        absorption = delta <= 0.0 and price_progress > 0.0
        if aligned_body and closes_away and local_control and (initiative or absorption):
            return {
                "departure_index": departure,
                "touch_index": touch,
                "response_index": index,
                "retest_extreme": float(retest_extreme),
                "response_kind": "ALIGNED_INITIATIVE" if initiative else "ADVERSE_FLOW_ABSORBED",
                "departure_minutes": float(departure - setup.confirmation_index),
                "return_wait_minutes": float(touch - departure),
                "response_delay_minutes": float(index - touch),
                "response_delta_signed": delta,
                "response_body_ratio": _finite(row.get("body_ratio"), 0.0),
                "response_range_ratio": _finite(row.get("range_ratio"), 0.0),
                "response_activity_ratio": _finite(row.get("activity_ratio"), 0.0),
                "response_impact_per_activity": _finite(row.get("impact_per_activity"), 0.0),
            }
    return None


def _target_level(
    levels: Sequence[LiquidityLevel],
    source: LiquidityLevel,
    side: str,
    emission_index: int,
    entry: float,
) -> LiquidityLevel | None:
    target_side = "HIGH" if side == "LONG" else "LOW"
    candidates = [
        level for level in levels
        if level.side == target_side
        and level.timeframe_minutes >= source.timeframe_minutes
        and level.observed_index_1m < emission_index
        and (level.first_penetration_index is None or level.first_penetration_index > emission_index)
        and ((side == "LONG" and level.price > entry) or (side == "SHORT" and level.price < entry))
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda level: abs(level.price - entry))
    return candidates[0]


def _volume_profile_features(data: pd.DataFrame, emission_index: int, entry: float, target: float) -> dict[str, float]:
    history = data.iloc[max(0, emission_index - 1440):emission_index]
    if len(history) < 240:
        return {}
    low, high = float(history.low.min()), float(history.high.max())
    if high <= low:
        return {}
    bins = np.linspace(low, high, 65)
    typical = ((history.high + history.low + history.close) / 3.0).to_numpy(float)
    weights = history.quote_volume.to_numpy(float)
    hist, edges = np.histogram(typical, bins=bins, weights=weights)
    if hist.sum() <= 0:
        return {}
    density = hist / hist.sum()
    centers = (edges[:-1] + edges[1:]) / 2.0
    path_low, path_high = sorted((entry, target))
    path_mask = (centers >= path_low) & (centers <= path_high)
    path = density[path_mask]
    target_bin = min(max(int(np.searchsorted(edges, target, side="right") - 1), 0), len(density) - 1)
    entry_bin = min(max(int(np.searchsorted(edges, entry, side="right") - 1), 0), len(density) - 1)
    low_node_threshold = float(np.quantile(density, 0.35))
    return {
        "profile_entry_density": float(density[entry_bin]),
        "profile_target_density": float(density[target_bin]),
        "profile_path_mean_density": float(path.mean()) if len(path) else 0.0,
        "profile_path_low_volume_fraction": float((path <= low_node_threshold).mean()) if len(path) else 0.0,
        "profile_path_max_density": float(path.max()) if len(path) else 0.0,
    }


def _clock_features(timestamp: pd.Timestamp) -> dict[str, float]:
    minute = int(timestamp.minute)
    quarter = minute % 15
    return {
        "minute_of_hour": float(minute),
        "macro_50_to_10": float(minute >= 50 or minute <= 10),
        "distance_to_hour_boundary_minutes": float(min(minute, 60 - minute)),
        "quarter_hour_phase": float(quarter),
        "distance_to_quarter_hour_minutes": float(min(quarter, 15 - quarter)),
        "hour_utc_sin": math.sin(2.0 * math.pi * timestamp.hour / 24.0),
        "hour_utc_cos": math.cos(2.0 * math.pi * timestamp.hour / 24.0),
    }


def _make_action(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[LiquidityLevel],
    source: LiquidityLevel,
    setup: Setup,
    response: dict[str, Any],
    tick: float,
) -> ActionSpec | None:
    emission_index = int(response["response_index"])
    emission_ns = _time_ns(data.index, emission_index)
    entry = float(data.iloc[emission_index]["close"])
    buffer = max(2.0 * tick, 0.05 * _finite(data.iloc[emission_index].get("prior_range_1m"), tick))
    stop = setup.event_extreme - buffer if setup.side == "LONG" else setup.event_extreme + buffer
    if (setup.side == "LONG" and stop >= entry) or (setup.side == "SHORT" and stop <= entry):
        return None
    target_level = _target_level(levels, source, setup.side, emission_index, entry)
    if target_level is None:
        return None
    target = target_level.price
    economics = _economics(side=setup.side, entry=entry, stop=stop, target=target, tick_size=tick, entry_style="MARKET")
    if not economics or economics["gross_rr"] < 1.0 or economics["target_net_r"] <= 0.0 or economics["stop_net_r"] >= 0.0:
        return None
    event_ns = _time_ns(data.index, setup.interaction_index)
    episode_id = f"HLBPR:{symbol}:{event_ns}:{_stable_id(source.level_id)}"
    action_id = f"{episode_id}:{setup.setup_kind}:{response['response_kind']}"
    row = data.iloc[emission_index]
    interaction_row = data.iloc[setup.interaction_index]
    reclaim_row = data.iloc[setup.reclaim_index]
    features: dict[str, Any] = {
        **economics,
        **_balance_features(data, setup.interaction_index),
        **_volume_profile_features(data, emission_index, entry, target),
        **_clock_features(pd.Timestamp(data.index[setup.interaction_index])),
        "setup_kind": setup.setup_kind,
        "response_kind": response["response_kind"],
        "source_side": source.side,
        "source_scale_minutes": source.timeframe_minutes,
        "source_strength_ratio": source.strength_ratio,
        "source_defense_count": source.defense_count,
        "source_age_minutes": (emission_ns - source.observed_time_ns) / 60_000_000_000.0,
        "target_scale_minutes": target_level.timeframe_minutes,
        "target_scale_ratio": target_level.timeframe_minutes / max(source.timeframe_minutes, 1),
        "target_strength_ratio": target_level.strength_ratio,
        "target_defense_count": target_level.defense_count,
        "target_age_minutes": (emission_ns - target_level.observed_time_ns) / 60_000_000_000.0,
        "manipulation_penetration_bps": abs(setup.event_extreme - source.price) / max(abs(source.price), EPS) * 10_000.0,
        "manipulation_duration_minutes": float(setup.reclaim_index - setup.interaction_index),
        "reclaim_close_from_boundary_bps": _sign(setup.side) * (float(reclaim_row.close) - source.price) / max(abs(source.price), EPS) * 10_000.0,
        "pre_event_control_distance_bps": abs(setup.pre_event_control - source.price) / max(abs(source.price), EPS) * 10_000.0,
        "zone_width_bps": (setup.upper - setup.lower) / max(abs(entry), EPS) * 10_000.0,
        "directional_gap_body_ratio": setup.directional_gap.middle_body_ratio,
        "directional_gap_range_ratio": setup.directional_gap.middle_range_ratio,
        "directional_gap_activity_ratio": setup.directional_gap.middle_activity_ratio,
        "directional_gap_delta_signed": setup.directional_gap.middle_delta_signed,
        "manipulation_gap_present": float(setup.manipulation_gap is not None),
        "manipulation_gap_body_ratio": setup.manipulation_gap.middle_body_ratio if setup.manipulation_gap else 0.0,
        "manipulation_gap_range_ratio": setup.manipulation_gap.middle_range_ratio if setup.manipulation_gap else 0.0,
        "manipulation_gap_activity_ratio": setup.manipulation_gap.middle_activity_ratio if setup.manipulation_gap else 0.0,
        **{key: value for key, value in response.items() if key not in {"departure_index", "touch_index", "response_index", "retest_extreme", "response_kind"}},
        "event_delta_share_outward": (-_sign(setup.side)) * _finite(interaction_row.get("delta_share"), 0.0),
        "event_activity_ratio": _finite(interaction_row.get("activity_ratio"), 0.0),
        "event_basis_change_3m_outward": (-_sign(setup.side)) * _finite(interaction_row.get("basis_change_3m_bps"), 0.0),
        "event_oi_change_1": _finite(interaction_row.get("metric_oi_log_change_1"), 0.0),
        "event_oi_change_3": _finite(interaction_row.get("metric_oi_log_change_3"), 0.0),
        "decision_delta_share_signed": _sign(setup.side) * _finite(row.get("delta_share"), 0.0),
        "decision_activity_ratio": _finite(row.get("activity_ratio"), 0.0),
        "decision_basis_bps_signed": _sign(setup.side) * _finite(row.get("basis_bps"), 0.0),
        "decision_index_return_5m_signed": _sign(setup.side) * _finite(row.get("index_return_5m"), 0.0),
        "decision_futures_return_5m_signed": _sign(setup.side) * _finite(row.get("futures_return_5m"), 0.0),
        # diagnostic-only geometry, excluded from model contracts
        "diagnostic_event_time_ns": _time_ns(data.index, setup.interaction_index),
        "diagnostic_reclaim_time_ns": _time_ns(data.index, setup.reclaim_index),
        "diagnostic_confirmation_time_ns": _time_ns(data.index, setup.confirmation_index),
        "diagnostic_departure_time_ns": _time_ns(data.index, int(response["departure_index"])),
        "diagnostic_first_return_time_ns": _time_ns(data.index, int(response["touch_index"])),
        "diagnostic_response_time_ns": emission_ns,
        "diagnostic_event_extreme": setup.event_extreme,
        "diagnostic_zone_lower": setup.lower,
        "diagnostic_zone_upper": setup.upper,
        "diagnostic_retest_extreme": response["retest_extreme"],
        "diagnostic_target_level_id": target_level.level_id,
    }
    for minutes in (1, 3, 5, 15, 30, 60):
        features[f"common_return_{minutes}m_signed"] = _sign(setup.side) * _finite(row.get(f"common_return_{minutes}m"), 0.0)
        features[f"residual_return_{minutes}m_signed"] = _sign(setup.side) * _finite(row.get(f"residual_return_{minutes}m"), 0.0)
        features[f"common_breadth_{minutes}m_signed"] = _sign(setup.side) * _finite(row.get(f"common_breadth_{minutes}m"), 0.0)
    for column in row.index:
        if str(column).startswith("metric_"):
            features[str(column)] = _finite(row[column], 0.0)
    return ActionSpec(
        action_id=action_id,
        episode_id=episode_id,
        symbol=symbol,
        event_type="HIERARCHICAL_LIQUIDITY_ACQUISITION",
        decision_stage=f"{setup.setup_kind}_FIRST_RETURN_RESPONSE",
        side=setup.side,
        emission_index=emission_index,
        emission_time_ns=emission_ns,
        entry_style="MARKET",
        entry=entry,
        stop=stop,
        target=target,
        entry_expiry_minutes=1,
        source_level_id=source.level_id,
        source_kind=source.source_kind,
        source_timeframe_minutes=source.timeframe_minutes,
        source_span=source.span,
        source_price=source.price,
        source_lower=source.lower,
        source_upper=source.upper,
        source_strength_ratio=source.strength_ratio,
        source_defense_count=source.defense_count,
        source_age_minutes=(emission_ns - source.observed_time_ns) / 60_000_000_000.0,
        objective_id=target_level.level_id,
        objective_kind=target_level.source_kind,
        objective_timeframe_minutes=target_level.timeframe_minutes,
        objective_strength_ratio=target_level.strength_ratio,
        interaction_time_ns=event_ns,
        feature_values=features,
    )


def _hierarchical_suppression(candidates: pd.DataFrame, data: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    if candidates.empty:
        return candidates, {"kept": 0, "suppressed_lower_resistance": 0, "suppressed_same_episode": 0}
    candidates = candidates.sort_values(["emission_index", "source_timeframe_minutes", "action_id"], ascending=[True, False, True]).copy()
    kept: list[pd.Series] = []
    ledger: Ledger | None = None
    suppressed_lower = 0
    suppressed_same = 0
    for _, row in candidates.iterrows():
        index = int(row.emission_index)
        if ledger is not None:
            path = data.iloc[ledger.emission_index:index + 1]
            target_hit = bool((path.high >= ledger.target_price).any()) if ledger.side == "LONG" else bool((path.low <= ledger.target_price).any())
            invalidated = bool((path.low <= ledger.invalidation_price).any()) if ledger.side == "LONG" else bool((path.high >= ledger.invalidation_price).any())
            if target_hit or invalidated:
                ledger = None
        if ledger is not None:
            countertrend = row.side != ledger.side
            inside_route = row.source_price < ledger.target_price if ledger.side == "LONG" else row.source_price > ledger.target_price
            if countertrend and inside_route and int(row.source_timeframe_minutes) <= ledger.source_timeframe_minutes:
                suppressed_lower += 1
                continue
            if row.side == ledger.side and abs(index - ledger.emission_index) <= 45:
                suppressed_same += 1
                continue
        kept.append(row)
        ledger = Ledger(
            side=str(row.side),
            source_timeframe_minutes=int(row.source_timeframe_minutes),
            source_price=float(row.source_price),
            target_price=float(row.target),
            invalidation_price=float(row.stop),
            emission_index=index,
            target_level_id=str(row.objective_id),
        )
    output = pd.DataFrame(kept).reset_index(drop=True) if kept else candidates.iloc[0:0].copy()
    return output, {"kept": len(output), "suppressed_lower_resistance": suppressed_lower, "suppressed_same_episode": suppressed_same}


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    raw: pd.DataFrame,
    levels: Sequence[LiquidityLevel],
    trading_start: date,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    tick = CONTRACTS[symbol].tick_size
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    records: list[dict[str, Any]] = []
    first_acquisitions = 0
    setups = 0
    for source in levels:
        interaction = source.first_penetration_index
        if interaction is None or interaction >= len(data) or _time_ns(data.index, interaction) < start_ns:
            continue
        first_acquisitions += 1
        manipulation = _detect_manipulation(data, interaction, source)
        if manipulation is None:
            continue
        reclaim, extreme = manipulation
        setup = _detect_setup(data, interaction, reclaim, extreme, source, tick)
        if setup is None:
            continue
        response = _first_return_response(data, setup, tick)
        if response is None:
            continue
        setups += 1
        action = _make_action(symbol, data, levels, source, setup, response, tick)
        if action is None:
            continue
        label = label_action(data, action, tick)
        if label.holding_minutes is not None and label.holding_minutes > MAX_HOLD_MINUTES:
            continue
        records.append({
            **{key: value for key, value in asdict(action).items() if key != "feature_values"},
            **action.feature_values,
            **asdict(label),
        })
    raw_candidates = pd.DataFrame(records)
    if not raw_candidates.empty and raw_candidates.action_id.duplicated().any():
        raise RuntimeError(f"duplicate hierarchical action id {symbol}")
    actions, suppression = _hierarchical_suppression(raw_candidates, data)
    summary = {
        "symbol": symbol,
        "bars": len(data),
        "hierarchical_levels": len(levels),
        "first_acquisitions": first_acquisitions,
        "complete_setups": setups,
        "raw_actions": len(raw_candidates),
        "actions": len(actions),
        "suppression": suppression,
        "outcomes": actions.outcome.value_counts().to_dict() if not actions.empty else {},
    }
    return actions, summary


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
    levels_by_symbol: dict[str, list[LiquidityLevel]] = {}
    for symbol in symbols:
        tick = CONTRACTS[symbol].tick_size
        raw = load_range_flow(symbol, load_start, end, cache)
        raw_by_symbol[symbol] = raw
        index_price = load_reference_range("indexPriceKlines", symbol, load_start, end, cache)
        mark_price = load_reference_range("markPriceKlines", symbol, load_start, end, cache)
        metrics = load_range_metrics(symbol, load_start, end, cache)
        data = prepare_market_state(raw, index_price, mark_price, metrics, tick)
        prepared[symbol] = data
        levels_by_symbol[symbol] = detect_hierarchical_liquidity(symbol, data, raw, tick)
    prepared = _add_common_state(prepared)
    frames: list[pd.DataFrame] = []
    by_symbol: dict[str, Any] = {}
    for symbol in symbols:
        actions, summary = generate_symbol(symbol, prepared[symbol], raw_by_symbol[symbol], levels_by_symbol[symbol], start)
        by_symbol[symbol] = summary
        if not actions.empty:
            actions.to_csv(output / f"{symbol}_hierarchical_actions.csv", index=False)
            frames.append(actions)
    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    combined.to_csv(output / "hierarchical_actions.csv", index=False)
    resolved = combined[combined.outcome.isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_FILL_TARGET_SAME_MINUTE", "AMBIGUOUS_SAME_MINUTE"])] if not combined.empty else combined
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


__all__ = ["run_research", "detect_hierarchical_liquidity", "_gap_at", "_detect_setup"]
