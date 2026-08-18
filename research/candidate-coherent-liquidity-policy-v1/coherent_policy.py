"""Direction-first, liquidity-owned intraday auction policy.

This module is a structural replacement rather than a filter on legacy plans.
The market is interpreted in one causal hierarchy:

1. maintain the still-unconsumed upper/lower liquidity map and active structure;
2. classify an interaction with external liquidity as failed auction or accepted auction;
3. use price/volume, basis/OI and cross-market state to describe who owns the move;
4. use BPR/IFVG/FVG/order-block/boundary retests only to refine the entry location;
5. enter after the first completed response, invalidate at the causal event extreme,
   and exit the whole position at the first opposing route obstacle which pays >= 1R.

Reversal and continuation are mutually exclusive explanations of the same source
interaction.  Tools do not vote as equal independent signals.  They occupy different
roles inside one narrative.  Future bars are used only by the offline destination and
first-passage labelers.
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

import hierarchical_liquidity_bpr as hl
import hierarchical_liquidity_bpr_v2 as hl2
from auction_episode_research import (
    CONTRACTS,
    ActionSpec,
    _economics,
    _stable_id,
    _time_ns,
)
from derivatives_dislocation import prepare_market_state
from liquidity_displacement import _add_common_state
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics


POLICY = (
    "DIRECTION_FIRST_LIQUIDITY_NARRATIVE:UNCONSUMED_LIQUIDITY_MAP_AND_ACTIVE_"
    "STRUCTURE_THEN_FAILED_OR_ACCEPTED_AUCTION_THEN_PRICE_VOLUME_OWNERSHIP_"
    "THEN_LOCATION_REFINEMENT_THEN_FIRST_COMPLETED_RESPONSE_THEN_NEXT_MINUTE_"
    "MARKET_ENTRY_TO_FIRST_OPPOSING_ROUTE_OBSTACLE"
)

NS_MINUTE = 60_000_000_000
MAX_RECLAIM_MINUTES = 12
MAX_ACCEPTANCE_MINUTES = 8
MAX_CONFIRM_MINUTES = 24
MAX_DEPARTURE_MINUTES = 12
MAX_RETURN_MINUTES = 45
MAX_RESPONSE_BARS = 3
MAX_HOLD_MINUTES = 360
ENTRY_SLIPPAGE_TICKS = 2
STOP_SLIPPAGE_TICKS = 2
TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
MINIMUM_SOURCE_TIMEFRAME = 15
EPS = 1e-12


@dataclass(frozen=True, slots=True)
class FixedHorizonLabel:
    fill_state: str
    outcome: str
    fill_index: int | None
    fill_time_ns: int | None
    resolution_index: int | None
    resolution_time_ns: int | None
    entry_wait_minutes: float | None
    holding_minutes: float | None
    actual_entry: float | None
    actual_target_net_r: float | None
    actual_stop_net_r: float | None
    net_r: float | None
    mfe_r: float | None
    mae_r: float | None


@dataclass(frozen=True, slots=True)
class DestinationLabel:
    state_id: str
    upper_level_id: str | None
    lower_level_id: str | None
    upper_price: float | None
    lower_price: float | None
    destination_label: str
    destination_resolution_index: int | None
    destination_resolution_time_ns: int | None


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _sign(side: str) -> float:
    if side == "LONG":
        return 1.0
    if side == "SHORT":
        return -1.0
    raise ValueError(side)


def _timestamp_ns(index: pd.DatetimeIndex, position: int) -> int:
    return int(index[position].value)


def _available_levels(
    levels: Sequence[hl.LiquidityLevel],
    index: int,
    *,
    side: str | None = None,
    minimum_timeframe: int = 5,
) -> list[hl.LiquidityLevel]:
    output: list[hl.LiquidityLevel] = []
    for level in levels:
        if level.timeframe_minutes < minimum_timeframe:
            continue
        if side is not None and level.side != side:
            continue
        if level.observed_index_1m >= index:
            continue
        if level.first_penetration_index is not None and level.first_penetration_index <= index:
            continue
        output.append(level)
    return output


def _nearest_route_level(
    levels: Sequence[hl.LiquidityLevel],
    index: int,
    price: float,
    side: str,
) -> hl.LiquidityLevel | None:
    wanted = "HIGH" if side == "LONG" else "LOW"
    candidates = [
        level
        for level in _available_levels(levels, index, side=wanted, minimum_timeframe=5)
        if (side == "LONG" and level.price > price) or (side == "SHORT" and level.price < price)
    ]
    candidates.sort(
        key=lambda level: (
            abs(level.price - price),
            -level.timeframe_minutes,
            -level.defense_count,
            -level.strength_ratio,
            level.level_id,
        )
    )
    return candidates[0] if candidates else None


def _nearest_two_sided_pools(
    levels: Sequence[hl.LiquidityLevel],
    index: int,
    price: float,
) -> tuple[hl.LiquidityLevel | None, hl.LiquidityLevel | None]:
    upper = [
        level for level in _available_levels(levels, index, side="HIGH")
        if level.price > price
    ]
    lower = [
        level for level in _available_levels(levels, index, side="LOW")
        if level.price < price
    ]
    key = lambda level: (
        abs(level.price - price),
        -level.timeframe_minutes,
        -level.defense_count,
        -level.strength_ratio,
        level.level_id,
    )
    upper.sort(key=key)
    lower.sort(key=key)
    return (upper[0] if upper else None, lower[0] if lower else None)


def _atr_price(data: pd.DataFrame, index: int, window: int = 60) -> float:
    start = max(0, index - window)
    frame = data.iloc[start:index]
    if frame.empty:
        return max(abs(float(data.iloc[index].close)) * 1e-4, EPS)
    previous = frame.close.shift(1)
    tr = pd.concat(
        [
            frame.high - frame.low,
            (frame.high - previous).abs(),
            (frame.low - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = _finite(tr.median(), 0.0)
    return max(value, abs(float(data.iloc[index].close)) * 1e-6, EPS)


def _level_weight(level: hl.LiquidityLevel, distance_atr: float) -> float:
    scale = math.sqrt(max(level.timeframe_minutes, 5) / 5.0)
    strength = max(0.15, _finite(level.strength_ratio, 0.15))
    defenses = 1.0 + math.log1p(max(0, int(level.defense_count) - 1))
    return scale * strength * defenses / max(distance_atr + 0.25, 0.25)


def _liquidity_map_features(
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    index: int,
) -> dict[str, float]:
    price = float(data.iloc[index].close)
    atr = _atr_price(data, index)
    upper, lower = _nearest_two_sided_pools(levels, index, price)
    output: dict[str, float] = {}
    for name, level in (("upper", upper), ("lower", lower)):
        if level is None:
            output.update(
                {
                    f"liquidity_{name}_present": 0.0,
                    f"liquidity_{name}_distance_atr": 99.0,
                    f"liquidity_{name}_distance_bps": 9999.0,
                    f"liquidity_{name}_scale_minutes": 0.0,
                    f"liquidity_{name}_strength": 0.0,
                    f"liquidity_{name}_defenses": 0.0,
                    f"liquidity_{name}_age_minutes": 0.0,
                    f"liquidity_{name}_attraction": 0.0,
                }
            )
            continue
        distance = abs(level.price - price)
        distance_atr = distance / atr
        output.update(
            {
                f"liquidity_{name}_present": 1.0,
                f"liquidity_{name}_distance_atr": distance_atr,
                f"liquidity_{name}_distance_bps": distance / max(abs(price), EPS) * 10_000.0,
                f"liquidity_{name}_scale_minutes": float(level.timeframe_minutes),
                f"liquidity_{name}_strength": _finite(level.strength_ratio, 0.0),
                f"liquidity_{name}_defenses": float(level.defense_count),
                f"liquidity_{name}_age_minutes": max(0.0, (index - level.observed_index_1m)),
                f"liquidity_{name}_attraction": _level_weight(level, distance_atr),
            }
        )
    upper_pull = output["liquidity_upper_attraction"]
    lower_pull = output["liquidity_lower_attraction"]
    output["liquidity_attraction_up_minus_down"] = upper_pull - lower_pull
    output["liquidity_attraction_normalized"] = (upper_pull - lower_pull) / max(upper_pull + lower_pull, EPS)
    if upper is not None and lower is not None and upper.price > lower.price:
        output["dealing_range_position"] = (price - lower.price) / (upper.price - lower.price)
        output["dealing_range_width_atr"] = (upper.price - lower.price) / atr
    else:
        output["dealing_range_position"] = 0.5
        output["dealing_range_width_atr"] = 0.0

    consumed = [
        level for level in levels
        if level.timeframe_minutes >= MINIMUM_SOURCE_TIMEFRAME
        and level.first_penetration_index is not None
        and level.first_penetration_index < index
    ]
    consumed.sort(key=lambda level: (int(level.first_penetration_index), level.timeframe_minutes))
    if consumed:
        last = consumed[-1]
        output["last_external_acquisition_side"] = 1.0 if last.side == "LOW" else -1.0
        output["last_external_acquisition_age_minutes"] = float(index - int(last.first_penetration_index))
        output["last_external_acquisition_scale_minutes"] = float(last.timeframe_minutes)
    else:
        output["last_external_acquisition_side"] = 0.0
        output["last_external_acquisition_age_minutes"] = 9999.0
        output["last_external_acquisition_scale_minutes"] = 0.0
    return output


def _level_event_position(data: pd.DataFrame, level: hl.LiquidityLevel) -> int:
    timestamp = pd.Timestamp(level.event_time_ns, unit="ns", tz="UTC")
    return int(data.index.searchsorted(timestamp, side="left"))


def _latest_levels(
    levels: Sequence[hl.LiquidityLevel],
    index: int,
    timeframe: int,
    side: str,
    count: int = 2,
) -> list[hl.LiquidityLevel]:
    candidates = [
        level for level in levels
        if level.timeframe_minutes == timeframe
        and level.side == side
        and level.observed_index_1m < index
    ]
    candidates.sort(key=lambda level: (level.event_time_ns, level.observed_time_ns, level.level_id))
    return candidates[-count:]


def _active_structure_features(
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    index: int,
) -> dict[str, float]:
    price = float(data.iloc[index].close)
    atr = _atr_price(data, index)
    output: dict[str, float] = {}
    votes: list[float] = []
    for timeframe in (15, 60, 240):
        highs = _latest_levels(levels, index, timeframe, "HIGH")
        lows = _latest_levels(levels, index, timeframe, "LOW")
        high_change = 0.0
        low_change = 0.0
        resistance_distance = 0.0
        support_distance = 0.0
        if len(highs) == 2:
            high_change = (highs[-1].price - highs[-2].price) / atr
            p0, p1 = _level_event_position(data, highs[-2]), _level_event_position(data, highs[-1])
            slope = (highs[-1].price - highs[-2].price) / max(p1 - p0, 1)
            projected = highs[-1].price + slope * max(index - p1, 0)
            resistance_distance = (projected - price) / atr
            output[f"structure_{timeframe}m_resistance_slope_atr_per_bar"] = slope / atr
            output[f"structure_{timeframe}m_resistance_distance_atr"] = resistance_distance
        else:
            output[f"structure_{timeframe}m_resistance_slope_atr_per_bar"] = 0.0
            output[f"structure_{timeframe}m_resistance_distance_atr"] = 0.0
        if len(lows) == 2:
            low_change = (lows[-1].price - lows[-2].price) / atr
            p0, p1 = _level_event_position(data, lows[-2]), _level_event_position(data, lows[-1])
            slope = (lows[-1].price - lows[-2].price) / max(p1 - p0, 1)
            projected = lows[-1].price + slope * max(index - p1, 0)
            support_distance = (price - projected) / atr
            output[f"structure_{timeframe}m_support_slope_atr_per_bar"] = slope / atr
            output[f"structure_{timeframe}m_support_distance_atr"] = support_distance
        else:
            output[f"structure_{timeframe}m_support_slope_atr_per_bar"] = 0.0
            output[f"structure_{timeframe}m_support_distance_atr"] = 0.0
        if high_change > 0.0 and low_change > 0.0:
            vote = 1.0
        elif high_change < 0.0 and low_change < 0.0:
            vote = -1.0
        else:
            vote = 0.0
        votes.append(vote)
        output[f"structure_{timeframe}m_high_change_atr"] = high_change
        output[f"structure_{timeframe}m_low_change_atr"] = low_change
        output[f"structure_{timeframe}m_trend_state"] = vote
        output[f"structure_{timeframe}m_channel_location"] = (
            support_distance / max(support_distance + resistance_distance, EPS)
            if support_distance > 0.0 and resistance_distance > 0.0
            else 0.5
        )
    output["structure_multiscale_trend_vote"] = float(np.mean(votes)) if votes else 0.0
    output["structure_multiscale_trend_agreement"] = abs(float(np.mean(votes))) if votes else 0.0
    return output


def _approach_features(
    data: pd.DataFrame,
    interaction: int,
    source: hl.LiquidityLevel,
) -> dict[str, float]:
    frame = data.iloc[max(0, interaction - 60):interaction]
    recent = data.iloc[max(0, interaction - 12):interaction]
    if len(frame) < 8:
        return {}
    closes = frame.close.to_numpy(float)
    changes = np.diff(closes)
    path = float(np.abs(changes).sum())
    net = float(closes[-1] - closes[0]) if len(closes) > 1 else 0.0
    toward = 1.0 if source.side == "HIGH" else -1.0
    signed_quote = 2.0 * frame.taker_buy_quote_volume - frame.quote_volume
    recent_signed = 2.0 * recent.taker_buy_quote_volume - recent.quote_volume
    price = max(abs(float(frame.close.iloc[-1])), EPS)
    output = {
        "approach_signed_net_bps": toward * net / price * 10_000.0,
        "approach_path_efficiency": abs(net) / max(path, EPS),
        "approach_delta_share_60m_toward": toward * _finite(signed_quote.sum() / max(frame.quote_volume.sum(), EPS), 0.0),
        "approach_delta_share_12m_toward": toward * _finite(recent_signed.sum() / max(recent.quote_volume.sum(), EPS), 0.0),
        "approach_activity_ratio_12m": _finite(recent.activity_ratio.median(), 0.0),
        "approach_range_ratio_12m": _finite(recent.range_ratio.median(), 0.0),
        "approach_impact_per_activity_12m": _finite(recent.impact_per_activity.median(), 0.0),
        "approach_touch_pressure": float(
            ((frame.low <= source.upper) & (frame.high >= source.lower)).sum()
        ),
    }
    return output


def _row_state_features(data: pd.DataFrame, index: int, side: str, prefix: str) -> dict[str, float]:
    row = data.iloc[index]
    sign = _sign(side)
    output = {
        f"{prefix}_body_bps_signed": sign * _finite(row.get("body"), float(row.close - row.open)) / max(abs(float(row.close)), EPS) * 10_000.0,
        f"{prefix}_delta_share_signed": sign * _finite(row.get("delta_share"), 0.0),
        f"{prefix}_activity_ratio": _finite(row.get("activity_ratio"), 0.0),
        f"{prefix}_range_ratio": _finite(row.get("range_ratio"), 0.0),
        f"{prefix}_body_ratio": _finite(row.get("body_ratio"), 0.0),
        f"{prefix}_trade_size_ratio": _finite(row.get("trade_size_ratio"), 0.0),
        f"{prefix}_impact_per_activity": _finite(row.get("impact_per_activity"), 0.0),
        f"{prefix}_close_location_signed": sign * (2.0 * _finite(row.get("close_location"), 0.5) - 1.0),
        f"{prefix}_basis_bps_signed": sign * _finite(row.get("basis_bps"), 0.0),
        f"{prefix}_basis_change_3m_signed": sign * _finite(row.get("basis_change_3m_bps"), 0.0),
        f"{prefix}_index_return_5m_signed": sign * _finite(row.get("index_return_5m"), 0.0),
        f"{prefix}_futures_return_5m_signed": sign * _finite(row.get("futures_return_5m"), 0.0),
        f"{prefix}_mark_deviation_bps_signed": sign * _finite(row.get("mark_index_bps"), 0.0),
        f"{prefix}_oi_change_1": _finite(row.get("metric_oi_log_change_1"), 0.0),
        f"{prefix}_oi_change_3": _finite(row.get("metric_oi_log_change_3"), 0.0),
    }
    for minutes in (1, 3, 5, 15, 30, 60):
        output[f"{prefix}_common_return_{minutes}m_signed"] = sign * _finite(row.get(f"common_return_{minutes}m"), 0.0)
        output[f"{prefix}_common_breadth_{minutes}m_signed"] = sign * _finite(row.get(f"common_breadth_{minutes}m"), 0.0)
        output[f"{prefix}_residual_return_{minutes}m_signed"] = sign * _finite(row.get(f"residual_return_{minutes}m"), 0.0)
    return output


def _clock_features(timestamp: pd.Timestamp) -> dict[str, float]:
    minute = int(timestamp.minute)
    hour = int(timestamp.hour)
    return {
        "clock_minute_of_hour": float(minute),
        "clock_distance_hour": float(min(minute, 60 - minute)),
        "clock_distance_quarter": float(min(minute % 15, 15 - minute % 15)),
        "clock_hour_sin": math.sin(2.0 * math.pi * hour / 24.0),
        "clock_hour_cos": math.cos(2.0 * math.pi * hour / 24.0),
    }


def _dedup_source_interactions(
    levels: Sequence[hl.LiquidityLevel],
    start_ns: int,
) -> list[hl.LiquidityLevel]:
    candidates = [
        level for level in levels
        if level.timeframe_minutes >= MINIMUM_SOURCE_TIMEFRAME
        and level.first_penetration_index is not None
        and level.observed_time_ns < start_ns + 10**20
    ]
    candidates.sort(
        key=lambda level: (
            int(level.first_penetration_index),
            level.side,
            -level.timeframe_minutes,
            -level.defense_count,
            -level.strength_ratio,
            level.level_id,
        )
    )
    kept: list[hl.LiquidityLevel] = []
    for level in candidates:
        interaction = int(level.first_penetration_index)
        overlapping = [
            prior for prior in kept
            if prior.side == level.side
            and abs(int(prior.first_penetration_index) - interaction) <= 10
        ]
        if not overlapping:
            kept.append(level)
            continue
        best = max(
            [level, *overlapping],
            key=lambda item: (
                item.timeframe_minutes,
                item.defense_count,
                item.strength_ratio,
                -abs(item.price - level.price),
            ),
        )
        if best is level:
            kept = [item for item in kept if item not in overlapping]
            kept.append(level)
    kept.sort(key=lambda level: (int(level.first_penetration_index), level.side, level.level_id))
    return kept


def _synthetic_gap(side: str, index: int, lower: float, upper: float, data: pd.DataFrame) -> hl.Gap:
    row = data.iloc[index]
    return hl.Gap(
        side=side,
        observed_index=index,
        lower=float(lower),
        upper=float(upper),
        middle_body_ratio=_finite(row.get("body_ratio"), 0.0),
        middle_range_ratio=_finite(row.get("range_ratio"), 0.0),
        middle_activity_ratio=_finite(row.get("activity_ratio"), 0.0),
        middle_delta_signed=_sign(side) * _finite(row.get("delta_share"), 0.0),
    )


def _last_opposite_order_block(
    data: pd.DataFrame,
    start: int,
    end: int,
    side: str,
    tick: float,
) -> tuple[float, float, int] | None:
    for index in range(end - 1, max(start, 0) - 1, -1):
        row = data.iloc[index]
        opposite = float(row.close - row.open) * _sign(side) < 0.0
        if not opposite:
            continue
        lower = min(float(row.open), float(row.close))
        upper = max(float(row.open), float(row.close))
        if upper - lower < 2.0 * tick:
            lower, upper = float(row.low), float(row.high)
        if upper - lower < tick:
            continue
        return lower, upper, index
    return None


def _reversal_setup(
    data: pd.DataFrame,
    source: hl.LiquidityLevel,
    tick: float,
) -> tuple[hl.Setup, dict[str, Any]] | None:
    interaction = int(source.first_penetration_index)
    manipulation = hl._detect_manipulation(data, interaction, source)
    if manipulation is None:
        return None
    reclaim, extreme = manipulation
    if reclaim - interaction > MAX_RECLAIM_MINUTES:
        return None
    setup = hl._detect_setup(data, interaction, reclaim, extreme, source, tick)
    if setup is None:
        return None
    ob = _last_opposite_order_block(data, interaction, setup.confirmation_index + 1, setup.side, tick)
    lower, upper = setup.lower, setup.upper
    location = setup.setup_kind
    ob_index = -1
    if ob is not None:
        ob_lower, ob_upper, ob_index = ob
        overlap_lower = max(lower, ob_lower)
        overlap_upper = min(upper, ob_upper)
        if overlap_upper > overlap_lower + tick:
            lower, upper = overlap_lower, overlap_upper
            location = f"{setup.setup_kind}_OB_OVERLAP"
    setup = hl.Setup(
        setup_kind=setup.setup_kind,
        side=setup.side,
        interaction_index=setup.interaction_index,
        reclaim_index=setup.reclaim_index,
        event_extreme=setup.event_extreme,
        confirmation_index=setup.confirmation_index,
        lower=lower,
        upper=upper,
        manipulation_gap=setup.manipulation_gap,
        directional_gap=setup.directional_gap,
        pre_event_control=setup.pre_event_control,
    )
    return setup, {
        "narrative_branch": "FAILED_AUCTION_REVERSAL",
        "location_kind": location,
        "order_block_index": float(ob_index),
    }


def _continuation_setup(
    data: pd.DataFrame,
    source: hl.LiquidityLevel,
    tick: float,
) -> tuple[hl.Setup, dict[str, Any]] | None:
    interaction = int(source.first_penetration_index)
    side = "LONG" if source.side == "HIGH" else "SHORT"
    sign = _sign(side)
    outside_index: int | None = None
    hold_index: int | None = None
    end = min(len(data), interaction + MAX_ACCEPTANCE_MINUTES + 1)
    for index in range(interaction, end):
        row = data.iloc[index]
        outside = float(row.close) > source.upper + tick if side == "LONG" else float(row.close) < source.lower - tick
        if outside_index is None:
            if outside:
                outside_index = index
            continue
        reclaimed = float(row.close) < source.lower if side == "LONG" else float(row.close) > source.upper
        if reclaimed:
            return None
        previous = data.iloc[index - 1]
        previous_outside = float(previous.close) > source.upper if side == "LONG" else float(previous.close) < source.lower
        aligned = sign * float(row.close - row.open) > 0.0
        if outside and previous_outside and aligned:
            hold_index = index
            break
    if outside_index is None or hold_index is None:
        return None

    directional_gaps = [
        gap for index in range(max(2, outside_index), min(len(data), hold_index + 4))
        if (gap := hl._gap_at(data, index, tick)) is not None and gap.side == side
    ]
    ob = _last_opposite_order_block(data, max(0, outside_index - 8), hold_index + 1, side, tick)
    lower, upper = source.lower, source.upper
    location = "TRANSFERRED_BOUNDARY"
    directional_gap = directional_gaps[-1] if directional_gaps else _synthetic_gap(side, hold_index, lower, upper, data)
    if directional_gaps:
        gap = directional_gaps[-1]
        overlap_lower = max(lower, gap.lower)
        overlap_upper = min(upper, gap.upper)
        if overlap_upper > overlap_lower + tick:
            lower, upper = overlap_lower, overlap_upper
            location = "BOUNDARY_FVG_OVERLAP"
    ob_index = -1
    if ob is not None:
        ob_lower, ob_upper, ob_index = ob
        overlap_lower = max(lower, ob_lower)
        overlap_upper = min(upper, ob_upper)
        if overlap_upper > overlap_lower + tick:
            lower, upper = overlap_lower, overlap_upper
            location = f"{location}_OB_OVERLAP"
    before = data.iloc[max(0, interaction - 10):interaction]
    pre_control = (
        float(before.low.min()) if side == "LONG" and not before.empty
        else float(before.high.max()) if side == "SHORT" and not before.empty
        else float(source.price)
    )
    event_extreme = (
        float(data.iloc[interaction:hold_index + 1].high.max())
        if side == "LONG"
        else float(data.iloc[interaction:hold_index + 1].low.min())
    )
    setup = hl.Setup(
        setup_kind="ACCEPTED_BREAK_HOLD",
        side=side,
        interaction_index=interaction,
        reclaim_index=outside_index,
        event_extreme=event_extreme,
        confirmation_index=hold_index,
        lower=float(lower),
        upper=float(upper),
        manipulation_gap=None,
        directional_gap=directional_gap,
        pre_event_control=pre_control,
    )
    return setup, {
        "narrative_branch": "ACCEPTED_AUCTION_CONTINUATION",
        "location_kind": location,
        "order_block_index": float(ob_index),
        "break_index": float(outside_index),
        "hold_index": float(hold_index),
    }


def _first_return_response(
    data: pd.DataFrame,
    setup: hl.Setup,
    tick: float,
) -> dict[str, Any] | None:
    side = setup.side
    sign = _sign(side)
    departure: int | None = None
    for index in range(
        setup.confirmation_index + 1,
        min(len(data), setup.confirmation_index + MAX_DEPARTURE_MINUTES + 1),
    ):
        close = float(data.iloc[index].close)
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
        overlaps = float(row.low) <= setup.upper and float(row.high) >= setup.lower
        if touch is None:
            if not overlaps:
                continue
            touch = index
            retest_extreme = float(row.low if side == "LONG" else row.high)
        else:
            retest_extreme = (
                min(float(retest_extreme), float(row.low))
                if side == "LONG"
                else max(float(retest_extreme), float(row.high))
            )
        if index - touch > MAX_RESPONSE_BARS:
            return None
        spent = float(row.close) < setup.lower - tick if side == "LONG" else float(row.close) > setup.upper + tick
        if spent:
            return None
        prior = data.iloc[index - 1]
        aligned_body = sign * float(row.close - row.open) > 0.0
        closes_away = float(row.close) >= setup.upper if side == "LONG" else float(row.close) <= setup.lower
        local_control = float(row.close) > float(prior.high) if side == "LONG" else float(row.close) < float(prior.low)
        delta = sign * _finite(row.get("delta_share"), 0.0)
        price_progress = sign * float(row.close - row.open)
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


def _action_stop(
    setup: hl.Setup,
    response: dict[str, Any],
    source: hl.LiquidityLevel,
    data: pd.DataFrame,
    tick: float,
    branch: str,
) -> float:
    index = int(response["response_index"])
    buffer = max(2.0 * tick, 0.05 * _atr_price(data, index))
    if branch == "FAILED_AUCTION_REVERSAL":
        reference = setup.event_extreme
    else:
        retest = float(response["retest_extreme"])
        reference = min(retest, source.lower) if setup.side == "LONG" else max(retest, source.upper)
    return reference - buffer if setup.side == "LONG" else reference + buffer


def _destination_label(
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    index: int,
    state_id: str,
) -> DestinationLabel:
    price = float(data.iloc[index].close)
    upper, lower = _nearest_two_sided_pools(levels, index, price)
    if upper is None or lower is None:
        return DestinationLabel(
            state_id,
            upper.level_id if upper else None,
            lower.level_id if lower else None,
            upper.price if upper else None,
            lower.price if lower else None,
            "UNRESOLVED_MISSING_SIDE",
            None,
            None,
        )
    end = min(len(data) - 1, index + MAX_HOLD_MINUTES)
    for position in range(index + 1, end + 1):
        row = data.iloc[position]
        up = float(row.high) >= upper.price
        down = float(row.low) <= lower.price
        if up and down:
            label = "AMBIGUOUS_SAME_MINUTE"
        elif up:
            label = "UPPER_FIRST"
        elif down:
            label = "LOWER_FIRST"
        else:
            continue
        return DestinationLabel(
            state_id,
            upper.level_id,
            lower.level_id,
            upper.price,
            lower.price,
            label,
            position,
            _timestamp_ns(data.index, position),
        )
    return DestinationLabel(
        state_id,
        upper.level_id,
        lower.level_id,
        upper.price,
        lower.price,
        "UNRESOLVED_HORIZON",
        None,
        None,
    )


def label_fixed_horizon(
    data: pd.DataFrame,
    action: ActionSpec,
    tick: float,
    max_hold_minutes: int = MAX_HOLD_MINUTES,
) -> FixedHorizonLabel:
    start = int(action.emission_index) + 1
    if start >= len(data):
        return FixedHorizonLabel("NO_FUTURE", "UNRESOLVED", None, None, None, None, None, None, None, None, None, None, None, None)
    sign = _sign(action.side)
    fill = float(data.iloc[start].open) + sign * ENTRY_SLIPPAGE_TICKS * tick
    stop_fill = float(action.stop) - sign * STOP_SLIPPAGE_TICKS * tick
    target_fill = float(action.target)
    if action.side == "LONG":
        valid = stop_fill < fill < target_fill
    else:
        valid = target_fill < fill < stop_fill
    if not valid:
        return FixedHorizonLabel(
            "GAP_INVALID_GEOMETRY",
            "UNFILLED",
            None,
            None,
            None,
            None,
            None,
            None,
            fill,
            None,
            None,
            None,
            None,
            None,
        )
    risk = abs(fill - stop_fill)
    target_gross = sign * (target_fill - fill) / risk
    stop_gross = sign * (stop_fill - fill) / risk
    target_cost = (TAKER_FEE * abs(fill) + MAKER_FEE * abs(target_fill)) / risk
    stop_cost = (TAKER_FEE * abs(fill) + TAKER_FEE * abs(stop_fill)) / risk
    target_net = target_gross - target_cost
    stop_net = stop_gross - stop_cost
    best = 0.0
    worst = 0.0
    end = min(len(data) - 1, start + max_hold_minutes)
    for index in range(start, end + 1):
        row = data.iloc[index]
        if action.side == "LONG":
            target_hit = float(row.high) >= action.target
            stop_hit = float(row.low) <= action.stop
            favorable = (float(row.high) - fill) / risk
            adverse = (float(row.low) - fill) / risk
        else:
            target_hit = float(row.low) <= action.target
            stop_hit = float(row.high) >= action.stop
            favorable = (fill - float(row.low)) / risk
            adverse = (fill - float(row.high)) / risk
        best = max(best, favorable)
        worst = min(worst, adverse)
        if target_hit and stop_hit:
            outcome, net = "AMBIGUOUS_SAME_MINUTE", stop_net
        elif stop_hit:
            outcome, net = "STOP_FIRST", stop_net
        elif target_hit:
            outcome, net = "TARGET_FIRST", target_net
        else:
            continue
        return FixedHorizonLabel(
            "FILLED_MARKET_NEXT_OPEN",
            outcome,
            start,
            _timestamp_ns(data.index, start),
            index,
            _timestamp_ns(data.index, index),
            1.0,
            float(index - start),
            fill,
            target_net,
            stop_net,
            net,
            best,
            worst,
        )
    exit_price = float(data.iloc[end].close) - sign * STOP_SLIPPAGE_TICKS * tick
    gross = sign * (exit_price - fill) / risk
    cost = (TAKER_FEE * abs(fill) + TAKER_FEE * abs(exit_price)) / risk
    net = gross - cost
    return FixedHorizonLabel(
        "FILLED_MARKET_NEXT_OPEN",
        "TIME_EXIT",
        start,
        _timestamp_ns(data.index, start),
        end,
        _timestamp_ns(data.index, end),
        1.0,
        float(end - start),
        fill,
        target_net,
        stop_net,
        net,
        best,
        worst,
    )


def _make_action(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    source: hl.LiquidityLevel,
    setup: hl.Setup,
    response: dict[str, Any],
    event_meta: dict[str, Any],
    tick: float,
) -> tuple[ActionSpec, DestinationLabel] | None:
    emission = int(response["response_index"])
    entry = float(data.iloc[emission].close)
    branch = str(event_meta["narrative_branch"])
    stop = _action_stop(setup, response, source, data, tick, branch)
    if (setup.side == "LONG" and stop >= entry) or (setup.side == "SHORT" and stop <= entry):
        return None
    target_level = _nearest_route_level(levels, emission, entry, setup.side)
    if target_level is None:
        return None
    target = float(target_level.price)
    economics = _economics(
        side=setup.side,
        entry=entry,
        stop=stop,
        target=target,
        tick_size=tick,
        entry_style="MARKET",
    )
    if not economics or economics["gross_rr"] < 1.0 or economics["target_net_r"] <= 0.0 or economics["stop_net_r"] >= 0.0:
        return None
    event_ns = _timestamp_ns(data.index, setup.interaction_index)
    state_id = f"CLPSTATE:{symbol}:{event_ns}:{branch}:{_stable_id(source.level_id, setup.setup_kind)}"
    episode_id = f"CLP:{symbol}:{event_ns}:{_stable_id(source.level_id)}"
    action_id = f"{episode_id}:{branch}:{setup.setup_kind}:{event_meta['location_kind']}:{response['response_kind']}"
    emission_ns = _timestamp_ns(data.index, emission)
    features: dict[str, Any] = {
        **economics,
        **_liquidity_map_features(data, levels, emission),
        **_active_structure_features(data, levels, emission),
        **_approach_features(data, setup.interaction_index, source),
        **_row_state_features(data, setup.interaction_index, setup.side, "event"),
        **_row_state_features(data, setup.confirmation_index, setup.side, "confirmation"),
        **_row_state_features(data, emission, setup.side, "decision"),
        **_clock_features(pd.Timestamp(data.index[emission])),
        **{key: value for key, value in response.items() if key not in {"departure_index", "touch_index", "response_index", "retest_extreme", "response_kind"}},
        "state_id": state_id,
        "narrative_branch": branch,
        "setup_kind": setup.setup_kind,
        "location_kind": event_meta["location_kind"],
        "response_kind": response["response_kind"],
        "source_side": source.side,
        "source_scale_minutes": float(source.timeframe_minutes),
        "source_strength_ratio": _finite(source.strength_ratio, 0.0),
        "source_defense_count": float(source.defense_count),
        "source_age_minutes": float(emission - source.observed_index_1m),
        "target_scale_minutes": float(target_level.timeframe_minutes),
        "target_strength_ratio": _finite(target_level.strength_ratio, 0.0),
        "target_defense_count": float(target_level.defense_count),
        "target_age_minutes": float(emission - target_level.observed_index_1m),
        "event_penetration_bps": abs(setup.event_extreme - source.price) / max(abs(source.price), EPS) * 10_000.0,
        "event_to_confirmation_minutes": float(setup.confirmation_index - setup.interaction_index),
        "zone_width_bps": (setup.upper - setup.lower) / max(abs(entry), EPS) * 10_000.0,
        "directional_gap_body_ratio": setup.directional_gap.middle_body_ratio,
        "directional_gap_range_ratio": setup.directional_gap.middle_range_ratio,
        "directional_gap_activity_ratio": setup.directional_gap.middle_activity_ratio,
        "directional_gap_delta_signed": setup.directional_gap.middle_delta_signed,
        "order_block_present": float(event_meta.get("order_block_index", -1.0) >= 0.0),
        "order_block_age_to_decision": float(emission - int(event_meta.get("order_block_index", emission))) if event_meta.get("order_block_index", -1.0) >= 0.0 else 0.0,
        "diagnostic_event_time_ns": event_ns,
        "diagnostic_confirmation_time_ns": _timestamp_ns(data.index, setup.confirmation_index),
        "diagnostic_departure_time_ns": _timestamp_ns(data.index, int(response["departure_index"])),
        "diagnostic_first_return_time_ns": _timestamp_ns(data.index, int(response["touch_index"])),
        "diagnostic_response_time_ns": emission_ns,
        "diagnostic_source_lower": source.lower,
        "diagnostic_source_upper": source.upper,
        "diagnostic_zone_lower": setup.lower,
        "diagnostic_zone_upper": setup.upper,
        "diagnostic_event_extreme": setup.event_extreme,
        "diagnostic_retest_extreme": response["retest_extreme"],
        "diagnostic_target_level_id": target_level.level_id,
    }
    action = ActionSpec(
        action_id=action_id,
        episode_id=episode_id,
        symbol=symbol,
        event_type=branch,
        decision_stage=f"{setup.setup_kind}_FIRST_RETURN_RESPONSE",
        side=setup.side,
        emission_index=emission,
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
        source_age_minutes=float(emission - source.observed_index_1m),
        objective_id=target_level.level_id,
        objective_kind=target_level.source_kind,
        objective_timeframe_minutes=target_level.timeframe_minutes,
        objective_strength_ratio=target_level.strength_ratio,
        interaction_time_ns=event_ns,
        feature_values=features,
    )
    return action, _destination_label(data, levels, emission, state_id)


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    raw: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    trading_start: date,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    tick = CONTRACTS[symbol].tick_size
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    source_levels = _dedup_source_interactions(levels, start_ns)
    action_records: list[dict[str, Any]] = []
    state_records: list[dict[str, Any]] = []
    counts = {
        "source_interactions": 0,
        "reversal_complete": 0,
        "continuation_complete": 0,
        "executable_actions": 0,
    }
    seen_actions: set[str] = set()
    for source in source_levels:
        interaction = int(source.first_penetration_index)
        if interaction >= len(data) or _timestamp_ns(data.index, interaction) < start_ns:
            continue
        counts["source_interactions"] += 1
        for detector in (_reversal_setup, _continuation_setup):
            detected = detector(data, source, tick)
            if detected is None:
                continue
            setup, meta = detected
            response = _first_return_response(data, setup, tick)
            if response is None:
                continue
            if meta["narrative_branch"] == "FAILED_AUCTION_REVERSAL":
                counts["reversal_complete"] += 1
            else:
                counts["continuation_complete"] += 1
            made = _make_action(symbol, data, levels, source, setup, response, meta, tick)
            if made is None:
                continue
            action, destination = made
            if action.action_id in seen_actions:
                continue
            seen_actions.add(action.action_id)
            label = label_fixed_horizon(data, action, tick)
            action_records.append(
                {
                    **{key: value for key, value in asdict(action).items() if key != "feature_values"},
                    **action.feature_values,
                    **asdict(label),
                }
            )
            state_records.append(
                {
                    "state_id": destination.state_id,
                    "symbol": symbol,
                    "episode_id": action.episode_id,
                    "emission_index": action.emission_index,
                    "emission_time_ns": action.emission_time_ns,
                    "action_side": action.side,
                    **action.feature_values,
                    **asdict(destination),
                }
            )
            counts["executable_actions"] += 1
    actions = pd.DataFrame(action_records)
    states = pd.DataFrame(state_records)
    if not actions.empty and actions.action_id.duplicated().any():
        raise RuntimeError(f"duplicate action identity for {symbol}")
    if not states.empty:
        states = states.drop_duplicates("state_id", keep="first").reset_index(drop=True)
    summary = {
        "symbol": symbol,
        "bars": int(len(data)),
        "levels": int(len(levels)),
        **counts,
        "outcomes": actions.outcome.value_counts().to_dict() if not actions.empty else {},
        "branches": actions.narrative_branch.value_counts().to_dict() if not actions.empty else {},
    }
    return actions, states, summary


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
    levels_by_symbol: dict[str, list[hl.LiquidityLevel]] = {}
    for symbol in symbols:
        tick = CONTRACTS[symbol].tick_size
        raw = load_range_flow(symbol, load_start, end, cache)
        index_price = load_reference_range("indexPriceKlines", symbol, load_start, end, cache)
        mark_price = load_reference_range("markPriceKlines", symbol, load_start, end, cache)
        metrics = load_range_metrics(symbol, load_start, end, cache)
        state = prepare_market_state(raw, index_price, mark_price, metrics, tick)
        prepared[symbol] = state
        raw_by_symbol[symbol] = raw
        levels_by_symbol[symbol] = hl2.detect_levels_v2(symbol, state, raw, tick)
    prepared = _add_common_state(prepared)

    action_frames: list[pd.DataFrame] = []
    state_frames: list[pd.DataFrame] = []
    by_symbol: dict[str, Any] = {}
    for symbol in symbols:
        actions, states, summary = generate_symbol(
            symbol,
            prepared[symbol],
            raw_by_symbol[symbol],
            levels_by_symbol[symbol],
            start,
        )
        by_symbol[symbol] = summary
        if not actions.empty:
            actions.to_csv(output / f"{symbol}_coherent_actions.csv", index=False)
            action_frames.append(actions)
        if not states.empty:
            states.to_csv(output / f"{symbol}_destination_states.csv", index=False)
            state_frames.append(states)
    combined_actions = pd.concat(action_frames, ignore_index=True, sort=False) if action_frames else pd.DataFrame()
    combined_states = pd.concat(state_frames, ignore_index=True, sort=False) if state_frames else pd.DataFrame()
    combined_actions.to_csv(output / "coherent_actions.csv", index=False)
    combined_states.to_csv(output / "destination_states.csv", index=False)
    resolved = (
        combined_actions[combined_actions.outcome.isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "TIME_EXIT"])]
        if not combined_actions.empty else combined_actions
    )
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "warmup_days": int(warmup_days),
        "symbols": list(symbols),
        "actions": int(len(combined_actions)),
        "destination_states": int(len(combined_states)),
        "resolved_actions": int(len(resolved)),
        "wins": int((resolved.outcome == "TARGET_FIRST").sum()) if not resolved.empty else 0,
        "win_rate": float((resolved.outcome == "TARGET_FIRST").mean()) if not resolved.empty else None,
        "mean_net_r": float(pd.to_numeric(resolved.net_r, errors="coerce").mean()) if not resolved.empty else None,
        "by_symbol": by_symbol,
        "policy": POLICY,
        "future_information_in_features": False,
        "future_information_in_labels_only": True,
        "execution": {
            "signal": "completed one-minute response",
            "fill": "next one-minute open plus two adverse ticks",
            "target": "fixed nearest pre-existing route obstacle, maker",
            "stop": "fixed structural invalidation plus two adverse ticks, taker",
            "same_minute": "stop first",
            "maximum_hold_minutes": MAX_HOLD_MINUTES,
        },
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


__all__ = [
    "POLICY",
    "run_research",
    "generate_symbol",
    "label_fixed_horizon",
]
