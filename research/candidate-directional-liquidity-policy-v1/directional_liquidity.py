"""Unified causal direction -> liquidity event -> location -> execution research.

This module is intentionally not an OB strategy, an FVG strategy, or a channel
strategy.  It builds one market narrative in the same order a skilled intraday
trader has to make decisions:

1. maintain a causal hierarchy of still-unconsumed liquidity;
2. observe whether an interaction with that liquidity is rejected or accepted;
3. freeze a directional route to the first meaningful opposing obstacle;
4. use displacement/FVG/OB/BPR only to obtain a good price on the first return;
5. invalidate the event or the defended retest and exit the full position at the
   first route obstacle.

Trendline/channel information is represented only as structural context.  Price,
aggressor flow, volume, futures/index/mark dislocation, open interest and the
cross-asset state are observations of ownership, not stand-alone entry signals.
Future bars are used solely by first-passage labelers.
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

import hierarchical_liquidity_bpr as structure
from auction_episode_research import (
    ActionSpec,
    CONTRACTS,
    _economics,
    _stable_id,
    _time_ns,
    label_action,
)
from derivatives_dislocation import prepare_market_state
from liquidity_displacement import _add_common_state
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics


POLICY = (
    "DIRECTIONAL_LIQUIDITY_NARRATIVE:HIERARCHICAL_UNSWEPT_LIQUIDITY_THEN_"
    "FAILED_OR_ACCEPTED_AUCTION_THEN_DIRECTIONAL_ROUTE_THEN_FRESH_"
    "DISPLACEMENT_LOCATION_THEN_FIRST_CONTROLLED_RETURN_RESPONSE_THEN_"
    "NEXT_MINUTE_EXECUTION_TO_FIRST_MEANINGFUL_ROUTE_OBSTACLE"
)

TARGET_PIVOT_SPANS: dict[int, tuple[int, ...]] = {
    5: (2, 4),
    15: (2, 4),
    60: (2,),
    240: (1, 2),
    720: (1,),
    1440: (1,),
}
MIN_SOURCE_TIMEFRAME = 15
MAX_RECLAIM_MINUTES = 8
MAX_EVENT_DECISION_MINUTES = 20
MAX_ROUTE_TO_ENTRY_MINUTES = 50
MAX_RESPONSE_BARS = 3
MAX_HOLD_MINUTES = 360
EPS = 1e-12


@dataclass(frozen=True, slots=True)
class Route:
    route_id: str
    symbol: str
    event_kind: str
    side: str
    source: structure.LiquidityLevel
    target: structure.LiquidityLevel
    interaction_index: int
    event_decision_index: int
    event_extreme: float
    invalidation: float
    pre_event_control: float
    outside_index: int | None
    reclaim_index: int | None
    hold_index: int | None
    directional_gap: structure.Gap | None
    manipulation_gap: structure.Gap | None
    route_features: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EntryLocation:
    kind: str
    lower: float
    upper: float
    observed_index: int
    departure_index: int


@dataclass(frozen=True, slots=True)
class RouteLabel:
    outcome: str
    resolution_index: int | None
    resolution_time_ns: int | None
    holding_minutes: float | None
    favorable_r: float
    adverse_r: float


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _sign(side: str) -> float:
    if side == "LONG":
        return 1.0
    if side == "SHORT":
        return -1.0
    raise ValueError(side)


def _outside(row: pd.Series, level: structure.LiquidityLevel) -> bool:
    return (
        float(row["close"]) > level.upper
        if level.side == "HIGH"
        else float(row["close"]) < level.lower
    )


def _inside(row: pd.Series, level: structure.LiquidityLevel) -> bool:
    return (
        float(row["close"]) <= level.lower
        if level.side == "HIGH"
        else float(row["close"]) >= level.upper
    )


def _event_side(level: structure.LiquidityLevel, accepted: bool) -> str:
    if accepted:
        return "LONG" if level.side == "HIGH" else "SHORT"
    return "SHORT" if level.side == "HIGH" else "LONG"


def _source_is_meaningful(level: structure.LiquidityLevel) -> bool:
    """Market-definition rule, not a fitted performance threshold.

    A lone five-minute turn is an internal obstacle, not a directional source.
    Fifteen-minute liquidity must have been defended/clustered; sixty-minute and
    completed day/week levels are external by construction.
    """
    if level.timeframe_minutes >= 60:
        return True
    if level.timeframe_minutes == 15 and level.defense_count >= 2:
        return True
    return "PREVIOUS_" in str(level.source_kind)


def _target_is_meaningful(level: structure.LiquidityLevel) -> bool:
    if level.timeframe_minutes >= 15:
        return True
    return level.timeframe_minutes == 5 and level.defense_count >= 2


def detect_levels(
    symbol: str,
    data: pd.DataFrame,
    raw: pd.DataFrame,
    tick: float,
) -> list[structure.LiquidityLevel]:
    original = structure.PIVOT_SPANS
    structure.PIVOT_SPANS = TARGET_PIVOT_SPANS
    try:
        levels = structure.detect_hierarchical_liquidity(symbol, data, raw, tick)
    finally:
        structure.PIVOT_SPANS = original
    return levels


def _available_levels(
    levels: Sequence[structure.LiquidityLevel],
    index: int,
) -> list[structure.LiquidityLevel]:
    return [
        level
        for level in levels
        if level.observed_index_1m < index
        and (level.first_penetration_index is None or level.first_penetration_index >= index)
    ]


def _first_route_target(
    levels: Sequence[structure.LiquidityLevel],
    *,
    side: str,
    index: int,
    price: float,
    exclude: str,
) -> structure.LiquidityLevel | None:
    wanted = "HIGH" if side == "LONG" else "LOW"
    candidates = [
        level
        for level in levels
        if level.level_id != exclude
        and level.side == wanted
        and _target_is_meaningful(level)
        and level.observed_index_1m < index
        and (level.first_penetration_index is None or level.first_penetration_index > index)
        and ((side == "LONG" and level.price > price) or (side == "SHORT" and level.price < price))
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


def _bar_flow_features(row: pd.Series, prefix: str, side: str | None = None) -> dict[str, float]:
    sign = _sign(side) if side else 1.0
    output = {
        f"{prefix}_activity_ratio": _finite(row.get("activity_ratio")),
        f"{prefix}_delta_share": sign * _finite(row.get("delta_share")),
        f"{prefix}_body_ratio": _finite(row.get("body_ratio")),
        f"{prefix}_range_ratio": _finite(row.get("range_ratio")),
        f"{prefix}_trade_size_ratio": _finite(row.get("trade_size_ratio")),
        f"{prefix}_impact_per_activity": _finite(row.get("impact_per_activity")),
        f"{prefix}_close_location_signed": sign * (2.0 * _finite(row.get("close_location"), 0.5) - 1.0),
    }
    for name in (
        "basis_bps",
        "basis_change_1m_bps",
        "basis_change_3m_bps",
        "futures_return_5m",
        "index_return_5m",
        "mark_return_5m",
    ):
        output[f"{prefix}_{name}_signed"] = sign * _finite(row.get(name))
    for name in (
        "metric_oi_log_change_1",
        "metric_oi_log_change_3",
        "metric_oi_log_change_6",
        "metric_taker_change_1",
        "metric_top_position_change_1",
        "metric_top_minus_all_account",
    ):
        output[f"{prefix}_{name}"] = _finite(row.get(name))
    return output


def _path_features(data: pd.DataFrame, start: int, end: int, side: str, prefix: str) -> dict[str, float]:
    frame = data.iloc[max(0, start):end + 1]
    if frame.empty:
        return {}
    sign = _sign(side)
    close = frame["close"].astype(float)
    log_close = np.log(close.clip(lower=EPS))
    changes = log_close.diff().dropna()
    net = sign * _finite(log_close.iloc[-1] - log_close.iloc[0]) * 1e4 if len(frame) > 1 else 0.0
    path = _finite(changes.abs().sum()) * 1e4
    signed_quote = 2.0 * frame["taker_buy_quote_volume"].astype(float) - frame["quote_volume"].astype(float)
    quote = frame["quote_volume"].astype(float).sum()
    return {
        f"{prefix}_minutes": float(max(0, end - start)),
        f"{prefix}_net_bps_signed": net,
        f"{prefix}_path_efficiency_signed": net / max(path, EPS),
        f"{prefix}_range_bps": (float(frame.high.max()) - float(frame.low.min())) / max(abs(float(close.iloc[-1])), EPS) * 1e4,
        f"{prefix}_delta_share_signed": sign * _finite(signed_quote.sum() / max(quote, EPS)),
        f"{prefix}_activity_median": _finite(frame.get("activity_ratio", pd.Series(dtype=float)).median()),
        f"{prefix}_impact_median": _finite(frame.get("impact_per_activity", pd.Series(dtype=float)).median()),
        f"{prefix}_turn_rate": _finite((np.sign(changes).diff().fillna(0.0) != 0.0).mean()),
    }


def _liquidity_map_features(
    levels: Sequence[structure.LiquidityLevel],
    index: int,
    price: float,
) -> dict[str, float]:
    available = _available_levels(levels, index)
    highs = [level for level in available if level.side == "HIGH" and _target_is_meaningful(level) and level.price > price]
    lows = [level for level in available if level.side == "LOW" and _target_is_meaningful(level) and level.price < price]
    highs.sort(key=lambda item: item.price - price)
    lows.sort(key=lambda item: price - item.price)
    output: dict[str, float] = {
        "liquidity_available_upper_count": float(len(highs)),
        "liquidity_available_lower_count": float(len(lows)),
    }
    for side_name, collection in (("upper", highs), ("lower", lows)):
        for rank in range(3):
            prefix = f"liquidity_{side_name}_{rank + 1}"
            if rank >= len(collection):
                output[f"{prefix}_distance_bps"] = 0.0
                output[f"{prefix}_scale_minutes"] = 0.0
                output[f"{prefix}_defense"] = 0.0
                output[f"{prefix}_strength"] = 0.0
                continue
            level = collection[rank]
            output[f"{prefix}_distance_bps"] = abs(level.price - price) / max(abs(price), EPS) * 1e4
            output[f"{prefix}_scale_minutes"] = float(level.timeframe_minutes)
            output[f"{prefix}_defense"] = float(level.defense_count)
            output[f"{prefix}_strength"] = float(level.strength_ratio)
    upper_distance = output.get("liquidity_upper_1_distance_bps", 0.0)
    lower_distance = output.get("liquidity_lower_1_distance_bps", 0.0)
    output["liquidity_nearest_distance_asymmetry"] = (lower_distance - upper_distance) / max(lower_distance + upper_distance, EPS)
    return output


def _diagonal_context(
    levels: Sequence[structure.LiquidityLevel],
    data: pd.DataFrame,
    index: int,
    side: str,
) -> dict[str, float]:
    """One active structural line per side; never an independent trigger."""
    now_ns = _time_ns(data.index, index)
    output: dict[str, float] = {}
    for pivot_side in ("LOW", "HIGH"):
        points = [
            level
            for level in levels
            if level.side == pivot_side
            and level.timeframe_minutes in (15, 60)
            and level.observed_index_1m < index
            and 0 < now_ns - level.event_time_ns <= 24 * 60 * 60 * 1_000_000_000
        ]
        points.sort(key=lambda level: level.event_time_ns)
        points = points[-2:]
        key = pivot_side.lower()
        if len(points) < 2 or points[-1].event_time_ns == points[-2].event_time_ns:
            output[f"structure_{key}_line_available"] = 0.0
            output[f"structure_{key}_line_slope_bps_hour"] = 0.0
            output[f"structure_{key}_line_distance_bps_signed"] = 0.0
            continue
        first, second = points
        hours = (second.event_time_ns - first.event_time_ns) / 3_600_000_000_000.0
        slope = (second.price - first.price) / max(hours, EPS)
        ahead = (now_ns - second.event_time_ns) / 3_600_000_000_000.0
        projected = second.price + slope * ahead
        price = float(data.iloc[index]["close"])
        output[f"structure_{key}_line_available"] = 1.0
        output[f"structure_{key}_line_slope_bps_hour"] = slope / max(abs(price), EPS) * 1e4
        output[f"structure_{key}_line_distance_bps_signed"] = _sign(side) * (price - projected) / max(abs(price), EPS) * 1e4
    output["structure_active_channel_width_bps"] = abs(
        output.get("structure_high_line_distance_bps_signed", 0.0)
        - output.get("structure_low_line_distance_bps_signed", 0.0)
    )
    return output


def _cross_asset_features(row: pd.Series, side: str) -> dict[str, float]:
    sign = _sign(side)
    output: dict[str, float] = {}
    for minutes in (1, 3, 5, 15, 30, 60):
        for stem in ("common_return", "residual_return", "common_breadth"):
            output[f"direction_{stem}_{minutes}m_signed"] = sign * _finite(row.get(f"{stem}_{minutes}m"))
    return output


def _gap_sequence(data: pd.DataFrame, start: int, end: int, tick: float) -> list[structure.Gap]:
    return [
        gap
        for index in range(max(2, start), min(len(data), end + 1))
        if (gap := structure._gap_at(data, index, tick)) is not None
    ]


def _pre_control(data: pd.DataFrame, interaction: int, side: str) -> float:
    frame = data.iloc[max(0, interaction - 20):interaction]
    if frame.empty:
        return float(data.iloc[interaction]["close"])
    return float(frame.high.max()) if side == "LONG" else float(frame.low.min())


def _breaks_control(row: pd.Series, control: float, side: str, tick: float) -> bool:
    return float(row["close"]) > control + tick if side == "LONG" else float(row["close"]) < control - tick


def _route_features(
    data: pd.DataFrame,
    levels: Sequence[structure.LiquidityLevel],
    source: structure.LiquidityLevel,
    target: structure.LiquidityLevel,
    interaction: int,
    decision: int,
    side: str,
    event_extreme: float,
    pre_control: float,
    event_kind: str,
) -> dict[str, Any]:
    row = data.iloc[decision]
    event = data.iloc[interaction]
    price = float(row["close"])
    target_distance = abs(target.price - price)
    invalidation_distance = abs(price - event_extreme)
    output: dict[str, Any] = {
        "route_event_kind": event_kind,
        "route_side": side,
        "route_source_kind": source.source_kind,
        "route_source_scale_minutes": float(source.timeframe_minutes),
        "route_source_strength": float(source.strength_ratio),
        "route_source_defense": float(source.defense_count),
        "route_source_age_minutes": (int(data.index[decision].value) - source.observed_time_ns) / 60_000_000_000.0,
        "route_target_kind": target.source_kind,
        "route_target_scale_minutes": float(target.timeframe_minutes),
        "route_target_strength": float(target.strength_ratio),
        "route_target_defense": float(target.defense_count),
        "route_target_age_minutes": (int(data.index[decision].value) - target.observed_time_ns) / 60_000_000_000.0,
        "route_target_distance_bps": target_distance / max(abs(price), EPS) * 1e4,
        "route_event_excursion_bps": abs(event_extreme - source.price) / max(abs(source.price), EPS) * 1e4,
        "route_pre_control_distance_bps": abs(pre_control - source.price) / max(abs(source.price), EPS) * 1e4,
        "route_geometry_reward_risk": target_distance / max(invalidation_distance, EPS),
    }
    output.update(_path_features(data, max(0, interaction - 30), interaction - 1, side, "route_approach"))
    output.update(_path_features(data, interaction, decision, side, "route_event_path"))
    output.update(_bar_flow_features(event, "route_interaction", side))
    output.update(_bar_flow_features(row, "route_decision", side))
    output.update(_liquidity_map_features(levels, decision, price))
    output.update(_diagonal_context(levels, data, decision, side))
    output.update(_cross_asset_features(row, side))
    try:
        output.update(structure._balance_features(data, interaction))
        output.update(structure._volume_profile_features(data, decision, price, target.price))
        output.update(structure._clock_features(pd.Timestamp(data.index[interaction])))
    except Exception:
        # These are contextual enrichments; their absence must not change the
        # causal event or create an alternative trading policy.
        pass
    return output


def _failed_route(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[structure.LiquidityLevel],
    source: structure.LiquidityLevel,
    interaction: int,
    tick: float,
) -> Route | None:
    side = _event_side(source, accepted=False)
    end = min(len(data), interaction + MAX_EVENT_DECISION_MINUTES + 1)
    extreme = float(data.iloc[interaction]["high"] if source.side == "HIGH" else data.iloc[interaction]["low"])
    reclaim: int | None = None
    for index in range(interaction, min(end, interaction + MAX_RECLAIM_MINUTES + 1)):
        row = data.iloc[index]
        extreme = max(extreme, float(row["high"])) if source.side == "HIGH" else min(extreme, float(row["low"]))
        if _inside(row, source):
            reclaim = index
            break
    if reclaim is None:
        return None
    control = _pre_control(data, interaction, side)
    decision: int | None = None
    directional_gap: structure.Gap | None = None
    gaps = _gap_sequence(data, interaction - 2, end - 1, tick)
    for index in range(reclaim + 1, end):
        row = data.iloc[index]
        invalidated = float(row["high"]) > extreme if side == "SHORT" else float(row["low"]) < extreme
        if invalidated:
            extreme = max(extreme, float(row["high"])) if side == "SHORT" else min(extreme, float(row["low"]))
        candidate_gap = next((gap for gap in gaps if gap.observed_index == index and gap.side == side), None)
        if _breaks_control(row, control, side, tick) and candidate_gap is not None:
            decision = index
            directional_gap = candidate_gap
            break
    if decision is None:
        return None
    target = _first_route_target(levels, side=side, index=decision, price=float(data.iloc[decision]["close"]), exclude=source.level_id)
    if target is None:
        return None
    manipulation_gap = next(
        (gap for gap in reversed(gaps) if gap.observed_index <= reclaim and gap.side != side),
        None,
    )
    route_id = f"DLR:{symbol}:{_time_ns(data.index, interaction)}:FAILED:{_stable_id(source.level_id)}"
    features = _route_features(data, levels, source, target, interaction, decision, side, extreme, control, "FAILED_AUCTION")
    features["route_reclaim_minutes"] = float(reclaim - interaction)
    return Route(route_id, symbol, "FAILED_AUCTION", side, source, target, interaction, decision, extreme, extreme, None, reclaim, None, directional_gap, manipulation_gap, features)


def _accepted_route(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[structure.LiquidityLevel],
    source: structure.LiquidityLevel,
    interaction: int,
    tick: float,
) -> Route | None:
    side = _event_side(source, accepted=True)
    end = min(len(data), interaction + MAX_EVENT_DECISION_MINUTES + 1)
    first_outside: int | None = None
    hold: int | None = None
    event_extreme = float(data.iloc[interaction]["high"] if side == "LONG" else data.iloc[interaction]["low"])
    break_origin_frame = data.iloc[max(0, interaction - 10):interaction]
    if break_origin_frame.empty:
        break_origin = float(data.iloc[interaction]["low"] if side == "LONG" else data.iloc[interaction]["high"])
    else:
        break_origin = float(break_origin_frame.low.min()) if side == "LONG" else float(break_origin_frame.high.max())
    for index in range(interaction, end):
        row = data.iloc[index]
        event_extreme = max(event_extreme, float(row["high"])) if side == "LONG" else min(event_extreme, float(row["low"]))
        if _inside(row, source) and first_outside is not None:
            return None
        if first_outside is None:
            if _outside(row, source):
                first_outside = index
            continue
        previous = data.iloc[index - 1]
        aligned = _sign(side) * float(row["close"] - row["open"]) > 0.0
        if _outside(previous, source) and _outside(row, source) and aligned:
            hold = index
            break
    if first_outside is None or hold is None:
        return None
    gaps = _gap_sequence(data, max(interaction - 2, 2), end - 1, tick)
    directional_gap = next((gap for gap in gaps if gap.side == side and first_outside <= gap.observed_index <= hold + 3), None)
    decision = hold
    if directional_gap is None:
        for index in range(hold + 1, min(end, hold + 4)):
            directional_gap = next((gap for gap in gaps if gap.side == side and gap.observed_index == index), None)
            if directional_gap is not None:
                decision = index
                break
    if directional_gap is None:
        return None
    target = _first_route_target(levels, side=side, index=decision, price=float(data.iloc[decision]["close"]), exclude=source.level_id)
    if target is None:
        return None
    route_id = f"DLR:{symbol}:{_time_ns(data.index, interaction)}:ACCEPTED:{_stable_id(source.level_id)}"
    features = _route_features(data, levels, source, target, interaction, decision, side, event_extreme, break_origin, "ACCEPTED_AUCTION")
    features["route_break_to_hold_minutes"] = float(hold - first_outside)
    return Route(route_id, symbol, "ACCEPTED_AUCTION", side, source, target, interaction, decision, event_extreme, break_origin, first_outside, None, hold, directional_gap, None, features)


def build_routes(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[structure.LiquidityLevel],
    trading_start: date,
    tick: float,
) -> list[Route]:
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    routes: list[Route] = []
    for source in levels:
        if source.timeframe_minutes < MIN_SOURCE_TIMEFRAME or not _source_is_meaningful(source):
            continue
        interaction = source.first_penetration_index
        if interaction is None or interaction >= len(data) or _time_ns(data.index, interaction) < start_ns:
            continue
        failed = _failed_route(symbol, data, levels, source, interaction, tick)
        accepted = None if failed is not None else _accepted_route(symbol, data, levels, source, interaction, tick)
        route = failed or accepted
        if route is not None:
            routes.append(route)
    routes.sort(key=lambda route: (route.event_decision_index, -route.source.timeframe_minutes, route.route_id))
    # Nearby levels often describe the same causal liquidity episode.  Keep the
    # largest contextual source rather than multiplying one cascade into trades.
    output: list[Route] = []
    for route in routes:
        if output and route.event_decision_index - output[-1].event_decision_index <= 15 and route.symbol == output[-1].symbol:
            prior = output[-1]
            same_episode = abs(route.source.price - prior.source.price) <= max(route.source.upper-route.source.lower, prior.source.upper-prior.source.lower) * 2.0
            if same_episode:
                if route.source.timeframe_minutes > prior.source.timeframe_minutes:
                    output[-1] = route
                continue
        output.append(route)
    return output


def _entry_location(route: Route, data: pd.DataFrame, tick: float) -> EntryLocation | None:
    side = route.side
    start = max(2, route.interaction_index - 2)
    end = min(len(data) - 1, route.event_decision_index + 3)
    gaps = _gap_sequence(data, start, end, tick)
    directional = [gap for gap in gaps if gap.side == side and gap.observed_index <= route.event_decision_index + 3]
    opposite = [gap for gap in gaps if gap.side != side and gap.observed_index <= route.event_decision_index]
    bpr: tuple[float, float, int] | None = None
    for dg in reversed(directional):
        for mg in reversed(opposite):
            lower, upper = max(dg.lower, mg.lower), min(dg.upper, mg.upper)
            if upper > lower + tick:
                bpr = (lower, upper, max(dg.observed_index, mg.observed_index))
                break
        if bpr:
            break
    impulse = data.iloc[max(route.interaction_index, route.event_decision_index - 8):route.event_decision_index + 1]
    ob: tuple[float, float, int] | None = None
    if not impulse.empty:
        for index in range(route.event_decision_index - 1, max(route.interaction_index - 1, route.event_decision_index - 10), -1):
            row = data.iloc[index]
            opposite_body = _sign(side) * float(row["close"] - row["open"]) < 0.0
            if opposite_body:
                ob = (min(float(row["open"]), float(row["close"])), max(float(row["open"]), float(row["close"])), index)
                break
    if bpr is not None:
        lower, upper, observed = bpr
        kind = "BPR"
    elif directional:
        gap = directional[-1]
        lower, upper, observed = gap.lower, gap.upper, gap.observed_index
        kind = "FVG"
        if ob is not None:
            overlap_lower, overlap_upper = max(lower, ob[0]), min(upper, ob[1])
            if overlap_upper > overlap_lower + tick:
                lower, upper, observed, kind = overlap_lower, overlap_upper, max(ob[2], observed), "FVG_OB_OVERLAP"
    elif ob is not None:
        lower, upper, observed = ob
        kind = "ORDER_BLOCK_ORIGIN"
    else:
        return None
    departure = route.event_decision_index
    for index in range(max(observed, route.event_decision_index), min(len(data), route.event_decision_index + 8)):
        close = float(data.iloc[index]["close"])
        if (side == "LONG" and close > upper + tick) or (side == "SHORT" and close < lower - tick):
            departure = index
            break
    return EntryLocation(kind, float(lower), float(upper), int(observed), int(departure))


def _first_return_response(
    route: Route,
    location: EntryLocation,
    data: pd.DataFrame,
    tick: float,
) -> dict[str, Any] | None:
    side = route.side
    start = max(location.departure_index + 1, route.event_decision_index + 1)
    end = min(len(data), route.event_decision_index + MAX_ROUTE_TO_ENTRY_MINUTES + 1)
    touch: int | None = None
    extreme: float | None = None
    for index in range(start, end):
        row = data.iloc[index]
        route_invalid = float(row["low"]) <= route.invalidation if side == "LONG" else float(row["high"]) >= route.invalidation
        target_spent = float(row["high"]) >= route.target.price if side == "LONG" else float(row["low"]) <= route.target.price
        if route_invalid or target_spent:
            return None
        overlaps = float(row["low"]) <= location.upper and float(row["high"]) >= location.lower
        if touch is None:
            if not overlaps:
                continue
            touch = index
            extreme = float(row["low"] if side == "LONG" else row["high"])
        else:
            extreme = min(float(extreme), float(row["low"])) if side == "LONG" else max(float(extreme), float(row["high"]))
        if index - touch > MAX_RESPONSE_BARS:
            return None
        spent = float(row["close"]) < location.lower - tick if side == "LONG" else float(row["close"]) > location.upper + tick
        if spent:
            return None
        prior = data.iloc[index - 1]
        body_aligned = _sign(side) * float(row["close"] - row["open"]) > 0.0
        closes_away = float(row["close"]) >= location.upper if side == "LONG" else float(row["close"]) <= location.lower
        control = float(row["close"]) > float(prior["high"]) if side == "LONG" else float(row["close"]) < float(prior["low"])
        price_progress = _sign(side) * float(row["close"] - row["open"])
        delta_signed = _sign(side) * _finite(row.get("delta_share"))
        initiative = delta_signed > 0.0
        absorption = delta_signed <= 0.0 and price_progress > 0.0 and _finite(row.get("impact_per_activity")) > 0.0
        if body_aligned and closes_away and control and (initiative or absorption):
            return {
                "touch_index": touch,
                "response_index": index,
                "retest_extreme": float(extreme),
                "response_kind": "ALIGNED_INITIATIVE" if initiative else "ADVERSE_FLOW_ABSORBED",
                "return_wait_minutes": float(touch - location.departure_index),
                "response_delay_minutes": float(index - touch),
                "response_delta_signed": delta_signed,
                "response_activity_ratio": _finite(row.get("activity_ratio")),
                "response_body_ratio": _finite(row.get("body_ratio")),
                "response_range_ratio": _finite(row.get("range_ratio")),
                "response_impact_per_activity": _finite(row.get("impact_per_activity")),
            }
    return None


def label_route(data: pd.DataFrame, route: Route) -> RouteLabel:
    side = route.side
    start = route.event_decision_index + 1
    risk = abs(float(data.iloc[route.event_decision_index]["close"]) - route.invalidation)
    if risk <= 0.0:
        return RouteLabel("UNRESOLVED", None, None, None, 0.0, 0.0)
    favorable = 0.0
    adverse = 0.0
    end = min(len(data), start + MAX_HOLD_MINUTES + 1)
    for index in range(start, end):
        row = data.iloc[index]
        if side == "LONG":
            target_hit = float(row["high"]) >= route.target.price
            invalid_hit = float(row["low"]) <= route.invalidation
            favorable = max(favorable, (float(row["high"]) - float(data.iloc[route.event_decision_index]["close"])) / risk)
            adverse = min(adverse, (float(row["low"]) - float(data.iloc[route.event_decision_index]["close"])) / risk)
        else:
            target_hit = float(row["low"]) <= route.target.price
            invalid_hit = float(row["high"]) >= route.invalidation
            favorable = max(favorable, (float(data.iloc[route.event_decision_index]["close"]) - float(row["low"])) / risk)
            adverse = min(adverse, (float(data.iloc[route.event_decision_index]["close"]) - float(row["high"])) / risk)
        if target_hit and invalid_hit:
            outcome = "ROUTE_INVALIDATION_FIRST"
        elif invalid_hit:
            outcome = "ROUTE_INVALIDATION_FIRST"
        elif target_hit:
            outcome = "ROUTE_TARGET_FIRST"
        else:
            continue
        return RouteLabel(outcome, index, _time_ns(data.index, index), float(index - start + 1), favorable, adverse)
    return RouteLabel("ROUTE_UNRESOLVED", None, None, None, favorable, adverse)


def make_action(
    route: Route,
    location: EntryLocation,
    response: dict[str, Any],
    data: pd.DataFrame,
    tick: float,
) -> ActionSpec | None:
    emission = int(response["response_index"])
    entry = float(data.iloc[emission]["close"])
    prior_range = _finite(data.iloc[emission].get("prior_range_1m"), tick)
    buffer = max(2.0 * tick, 0.05 * prior_range)
    if route.event_kind == "FAILED_AUCTION":
        stop_reference = route.event_extreme
    else:
        stop_reference = float(response["retest_extreme"])
        if route.side == "LONG":
            stop_reference = min(stop_reference, location.lower)
        else:
            stop_reference = max(stop_reference, location.upper)
    stop = stop_reference - buffer if route.side == "LONG" else stop_reference + buffer
    target = float(route.target.price)
    if route.side == "LONG" and not (stop < entry < target):
        return None
    if route.side == "SHORT" and not (target < entry < stop):
        return None
    economics = _economics(side=route.side, entry=entry, stop=stop, target=target, tick_size=tick, entry_style="MARKET")
    if not economics or economics["gross_rr"] < 1.0 or economics["target_net_r"] <= 0.0 or economics["stop_net_r"] >= 0.0:
        return None
    emission_ns = _time_ns(data.index, emission)
    row = data.iloc[emission]
    features: dict[str, Any] = {
        **route.route_features,
        **economics,
        "entry_location_kind": location.kind,
        "entry_zone_width_bps": (location.upper - location.lower) / max(abs(entry), EPS) * 1e4,
        "entry_location_age_minutes": float(emission - location.observed_index),
        "entry_departure_to_return_minutes": float(int(response["touch_index"]) - location.departure_index),
        "entry_route_progress_fraction": abs(entry - route.source.price) / max(abs(route.target.price - route.source.price), EPS),
        "entry_remaining_route_bps": abs(route.target.price - entry) / max(abs(entry), EPS) * 1e4,
        "entry_stop_uses_event_extreme": float(route.event_kind == "FAILED_AUCTION"),
        **{key: value for key, value in response.items() if key not in {"touch_index", "response_index", "retest_extreme", "response_kind"}},
        "entry_response_kind": response["response_kind"],
        **_bar_flow_features(row, "entry_response", route.side),
        **_cross_asset_features(row, route.side),
        # Geometry for trade-by-trade diagnosis, excluded by model contracts.
        "diagnostic_event_time_ns": _time_ns(data.index, route.interaction_index),
        "diagnostic_route_decision_time_ns": _time_ns(data.index, route.event_decision_index),
        "diagnostic_location_observed_time_ns": _time_ns(data.index, location.observed_index),
        "diagnostic_departure_time_ns": _time_ns(data.index, location.departure_index),
        "diagnostic_first_return_time_ns": _time_ns(data.index, int(response["touch_index"])),
        "diagnostic_response_time_ns": emission_ns,
        "diagnostic_event_extreme": route.event_extreme,
        "diagnostic_location_lower": location.lower,
        "diagnostic_location_upper": location.upper,
        "diagnostic_retest_extreme": float(response["retest_extreme"]),
    }
    action_id = f"{route.route_id}:{location.kind}:{response['response_kind']}"
    return ActionSpec(
        action_id=action_id,
        episode_id=route.route_id,
        symbol=route.symbol,
        event_type="DIRECTIONAL_LIQUIDITY_ROUTE",
        decision_stage=f"{route.event_kind}_{location.kind}_FIRST_RETURN_RESPONSE",
        side=route.side,
        emission_index=emission,
        emission_time_ns=emission_ns,
        entry_style="MARKET",
        entry=entry,
        stop=stop,
        target=target,
        entry_expiry_minutes=1,
        source_level_id=route.source.level_id,
        source_kind=route.source.source_kind,
        source_timeframe_minutes=route.source.timeframe_minutes,
        source_span=route.source.span,
        source_price=route.source.price,
        source_lower=route.source.lower,
        source_upper=route.source.upper,
        source_strength_ratio=route.source.strength_ratio,
        source_defense_count=route.source.defense_count,
        source_age_minutes=(emission_ns - route.source.observed_time_ns) / 60_000_000_000.0,
        objective_id=route.target.level_id,
        objective_kind=route.target.source_kind,
        objective_timeframe_minutes=route.target.timeframe_minutes,
        objective_strength_ratio=route.target.strength_ratio,
        interaction_time_ns=_time_ns(data.index, route.interaction_index),
        feature_values=features,
    )


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    raw: pd.DataFrame,
    levels: Sequence[structure.LiquidityLevel],
    trading_start: date,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    tick = CONTRACTS[symbol].tick_size
    routes = build_routes(symbol, data, levels, trading_start, tick)
    route_records: list[dict[str, Any]] = []
    action_records: list[dict[str, Any]] = []
    for route in routes:
        route_label = label_route(data, route)
        route_records.append({
            "route_id": route.route_id,
            "symbol": route.symbol,
            "event_kind": route.event_kind,
            "side": route.side,
            "interaction_index": route.interaction_index,
            "route_decision_index": route.event_decision_index,
            "interaction_time_ns": _time_ns(data.index, route.interaction_index),
            "route_decision_time_ns": _time_ns(data.index, route.event_decision_index),
            "route_source_price": route.source.price,
            "route_target_price": route.target.price,
            "route_invalidation_price": route.invalidation,
            **route.route_features,
            **{f"route_label_{key}": value for key, value in asdict(route_label).items()},
        })
        location = _entry_location(route, data, tick)
        if location is None:
            continue
        response = _first_return_response(route, location, data, tick)
        if response is None:
            continue
        action = make_action(route, location, response, data, tick)
        if action is None:
            continue
        label = label_action(data, action, tick)
        if label.holding_minutes is not None and label.holding_minutes > MAX_HOLD_MINUTES:
            continue
        action_records.append({
            **{key: value for key, value in asdict(action).items() if key != "feature_values"},
            **action.feature_values,
            **asdict(label),
        })
    route_frame = pd.DataFrame(route_records)
    action_frame = pd.DataFrame(action_records)
    if not route_frame.empty and route_frame.route_id.duplicated().any():
        raise RuntimeError(f"duplicate route identity {symbol}")
    if not action_frame.empty and action_frame.action_id.duplicated().any():
        raise RuntimeError(f"duplicate action identity {symbol}")
    summary = {
        "symbol": symbol,
        "bars": int(len(data)),
        "levels": int(len(levels)),
        "meaningful_sources": int(sum(_source_is_meaningful(level) and level.timeframe_minutes >= MIN_SOURCE_TIMEFRAME for level in levels)),
        "routes": int(len(route_frame)),
        "actions": int(len(action_frame)),
        "route_outcomes": route_frame.get("route_label_outcome", pd.Series(dtype=str)).value_counts().to_dict(),
        "action_outcomes": action_frame.get("outcome", pd.Series(dtype=str)).value_counts().to_dict(),
    }
    return route_frame, action_frame, summary


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
    levels_by_symbol: dict[str, list[structure.LiquidityLevel]] = {}
    for symbol in symbols:
        tick = CONTRACTS[symbol].tick_size
        raw = load_range_flow(symbol, load_start, end, cache)
        raw_by_symbol[symbol] = raw
        index_price = load_reference_range("indexPriceKlines", symbol, load_start, end, cache)
        mark_price = load_reference_range("markPriceKlines", symbol, load_start, end, cache)
        metrics = load_range_metrics(symbol, load_start, end, cache)
        state = prepare_market_state(raw, index_price, mark_price, metrics, tick)
        prepared[symbol] = state
        levels_by_symbol[symbol] = detect_levels(symbol, state, raw, tick)
    prepared = _add_common_state(prepared)

    route_frames: list[pd.DataFrame] = []
    action_frames: list[pd.DataFrame] = []
    summaries: dict[str, Any] = {}
    for symbol in symbols:
        routes, actions, summary = generate_symbol(
            symbol,
            prepared[symbol],
            raw_by_symbol[symbol],
            levels_by_symbol[symbol],
            start,
        )
        summaries[symbol] = summary
        if not routes.empty:
            routes.to_csv(output / f"{symbol}_routes.csv", index=False)
            route_frames.append(routes)
        if not actions.empty:
            actions.to_csv(output / f"{symbol}_actions.csv", index=False)
            action_frames.append(actions)
    routes = pd.concat(route_frames, ignore_index=True, sort=False) if route_frames else pd.DataFrame()
    actions = pd.concat(action_frames, ignore_index=True, sort=False) if action_frames else pd.DataFrame()
    routes.to_csv(output / "routes.csv", index=False)
    actions.to_csv(output / "actions.csv", index=False)
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "warmup_days": warmup_days,
        "symbols": list(symbols),
        "routes": int(len(routes)),
        "actions": int(len(actions)),
        "by_symbol": summaries,
        "policy": POLICY,
        "future_information_in_features": False,
        "future_information_in_route_and_action_labels_only": True,
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


__all__ = [
    "POLICY",
    "Route",
    "EntryLocation",
    "RouteLabel",
    "build_routes",
    "detect_levels",
    "generate_symbol",
    "run_research",
]
