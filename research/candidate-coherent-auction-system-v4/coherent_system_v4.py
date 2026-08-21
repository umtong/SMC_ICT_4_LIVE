"""Coherent auction system v4: semantic direction, event ownership, executable geometry.

This version adds only mechanisms which solve observed structural problems:

* the source is an unconsumed semantic liquidity object, not every pivot;
* failed and accepted auctions are mutually exclusive terminal explanations;
* OB/FVG/BPR/IFVG refine a first-return entry rather than vote on direction;
* both confirmed-market and resting-limit entries are legitimate counterfactual
  actions, with causal fill/cancel handling;
* event and retest invalidations are separate geometries where economically valid;
* the full position exits at the first meaningful horizontal or historical-volume
  route obstacle; it never skips an obstacle merely to manufacture a high RR;
* every modeled stop including fees and slippage is normalized to exactly -1 account R.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence
import hashlib
import json
import math

import numpy as np
import pandas as pd

import coherent_policy as core
import coherent_policy_v2 as rich
import coherent_system as v3
import hierarchical_liquidity_bpr as hl
from auction_episode_research import CONTRACTS, ActionSpec, _economics, _stable_id
from derivatives_dislocation import prepare_market_state
from liquidity_displacement import _add_common_state
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics
from semantic_liquidity_v4 import PoolMeta, build_semantic_liquidity, direction_sources, route_levels


POLICY = (
    "SEMANTIC_LIQUIDITY_DIRECTION_ACTIVE_STRUCTURE_MUTUALLY_EXCLUSIVE_AUCTION_"
    "PRICE_VOLUME_OWNERSHIP_LOCATION_REFINEMENT_MARKET_OR_RESTING_FIRST_RETURN_"
    "ENTRY_STRUCTURAL_INVALIDATION_FIRST_MEANINGFUL_ROUTE_OBSTACLE"
)
MAX_HOLD_MINUTES = 360
LIMIT_EXPIRY_MINUTES = 20
ENTRY_SLIPPAGE_TICKS = 2
STOP_SLIPPAGE_TICKS = 2
LIMIT_TRADE_THROUGH_TICKS = 1
TARGET_INSIDE_TICKS = 1
TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
EPS = 1e-12


@dataclass(frozen=True, slots=True)
class Obstacle:
    obstacle_id: str
    kind: str
    timeframe_minutes: int
    structure_price: float
    order_price: float
    strength: float
    source_level_id: str | None


@dataclass(frozen=True, slots=True)
class ExecutionLabel:
    fill_state: str
    outcome: str
    fill_index: int | None
    fill_time_ns: int | None
    resolution_index: int | None
    resolution_time_ns: int | None
    entry_wait_minutes: float | None
    holding_minutes: float | None
    order_terminal_time_ns: int | None
    actual_entry: float | None
    actual_target_net_r: float | None
    actual_stop_net_r: float | None
    actual_gross_rr: float | None
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
    return 1.0 if side == "LONG" else -1.0


def _available_levels(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    index: int,
    side: str | None = None,
) -> list[hl.LiquidityLevel]:
    output: list[hl.LiquidityLevel] = []
    for level in route_levels(levels, metadata):
        if side is not None and level.side != side:
            continue
        if level.observed_index_1m >= index:
            continue
        if level.first_penetration_index is not None and int(level.first_penetration_index) <= index:
            continue
        output.append(level)
    return output


def _nearest_semantic_obstacle(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    index: int,
    entry: float,
    side: str,
    tick: float,
) -> Obstacle | None:
    wanted = "HIGH" if side == "LONG" else "LOW"
    candidates = [
        level for level in _available_levels(levels, metadata, index, wanted)
        if (side == "LONG" and level.price > entry + tick)
        or (side == "SHORT" and level.price < entry - tick)
    ]
    candidates.sort(
        key=lambda level: (
            abs(level.price - entry),
            -metadata[level.level_id].semantic_weight,
            -level.timeframe_minutes,
            level.level_id,
        )
    )
    if not candidates:
        return None
    level = candidates[0]
    order_price = float(level.price) - _sign(side) * TARGET_INSIDE_TICKS * tick
    return Obstacle(
        obstacle_id=level.level_id,
        kind=str(level.source_kind),
        timeframe_minutes=int(level.timeframe_minutes),
        structure_price=float(level.price),
        order_price=order_price,
        strength=float(metadata[level.level_id].semantic_weight),
        source_level_id=level.level_id,
    )


def _volume_node_obstacle(
    data: pd.DataFrame,
    index: int,
    entry: float,
    side: str,
    tick: float,
) -> tuple[Obstacle | None, dict[str, float]]:
    frame = data.iloc[max(0, index - 1440):index]
    if len(frame) < 180 or float(frame.quote_volume.sum()) <= 0.0:
        return None, {}
    typical = ((frame.high + frame.low + frame.close) / 3.0).to_numpy(float)
    weights = frame.quote_volume.to_numpy(float)
    finite = np.isfinite(typical) & np.isfinite(weights) & (weights > 0)
    typical, weights = typical[finite], weights[finite]
    if len(typical) < 180:
        return None, {}
    lower, upper = np.quantile(typical, [0.01, 0.99])
    if not np.isfinite(lower) or not np.isfinite(upper) or upper <= lower + tick:
        return None, {}
    edges = np.linspace(lower, upper, 65)
    histogram, _ = np.histogram(typical, bins=edges, weights=weights)
    positive = histogram[histogram > 0]
    if len(positive) < 8:
        return None, {}
    threshold = float(np.quantile(positive, 0.75))
    peaks = []
    for bin_index, value in enumerate(histogram):
        left = histogram[bin_index - 1] if bin_index > 0 else -np.inf
        right = histogram[bin_index + 1] if bin_index + 1 < len(histogram) else -np.inf
        if value >= threshold and value >= left and value >= right:
            peaks.append(bin_index)
    candidates: list[tuple[float, int]] = []
    for bin_index in peaks:
        zone_lower = float(edges[bin_index])
        zone_upper = float(edges[bin_index + 1])
        if side == "LONG" and zone_lower > entry + tick:
            target = zone_lower - TARGET_INSIDE_TICKS * tick
        elif side == "SHORT" and zone_upper < entry - tick:
            target = zone_upper + TARGET_INSIDE_TICKS * tick
        else:
            continue
        candidates.append((abs(target - entry), bin_index))
    if not candidates:
        return None, {
            "volume_route_node_count": float(len(peaks)),
            "volume_route_history_bars": float(len(frame)),
        }
    _, bin_index = min(candidates)
    zone_lower, zone_upper = float(edges[bin_index]), float(edges[bin_index + 1])
    target = zone_lower - tick if side == "LONG" else zone_upper + tick
    share = float(histogram[bin_index] / max(histogram.sum(), EPS))
    timestamp = int(data.index[index].value)
    obstacle = Obstacle(
        obstacle_id=f"VOLUME_NODE:{timestamp}:{bin_index}:{hashlib.sha1(f'{zone_lower}|{zone_upper}'.encode()).hexdigest()[:10]}",
        kind="CAUSAL_24H_VOLUME_NODE",
        timeframe_minutes=1440,
        structure_price=(zone_lower + zone_upper) / 2.0,
        order_price=target,
        strength=share,
        source_level_id=None,
    )
    features = {
        "volume_route_node_count": float(len(peaks)),
        "volume_route_history_bars": float(len(frame)),
        "volume_route_target_node_share": share,
        "volume_route_target_zone_width_bps": (zone_upper - zone_lower) / max(abs(entry), EPS) * 10_000.0,
        "volume_route_target_distance_bps": abs(target - entry) / max(abs(entry), EPS) * 10_000.0,
        "volume_route_total_profile_range_bps": (upper - lower) / max(abs(entry), EPS) * 10_000.0,
    }
    return obstacle, features


def _first_obstacle(
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    index: int,
    entry: float,
    side: str,
    tick: float,
) -> tuple[Obstacle | None, dict[str, float]]:
    semantic = _nearest_semantic_obstacle(levels, metadata, index, entry, side, tick)
    volume, volume_features = _volume_node_obstacle(data, index, entry, side, tick)
    available = [item for item in (semantic, volume) if item is not None]
    if not available:
        return None, volume_features
    chosen = min(available, key=lambda item: (abs(item.order_price - entry), -item.strength, item.obstacle_id))
    volume_features.update(
        {
            "route_obstacle_is_semantic_liquidity": float(chosen.source_level_id is not None),
            "route_obstacle_is_volume_node": float(chosen.kind == "CAUSAL_24H_VOLUME_NODE"),
            "route_obstacle_distance_bps": abs(chosen.order_price - entry) / max(abs(entry), EPS) * 10_000.0,
            "route_obstacle_strength": float(chosen.strength),
        }
    )
    return chosen, volume_features


def _nearest_two_sided(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    index: int,
    price: float,
) -> tuple[hl.LiquidityLevel | None, hl.LiquidityLevel | None]:
    upper = [level for level in _available_levels(levels, metadata, index, "HIGH") if level.price > price]
    lower = [level for level in _available_levels(levels, metadata, index, "LOW") if level.price < price]
    key = lambda level: (abs(level.price - price), -metadata[level.level_id].semantic_weight, -level.timeframe_minutes, level.level_id)
    upper.sort(key=key)
    lower.sort(key=key)
    return (upper[0] if upper else None, lower[0] if lower else None)


def _destination_label(data, levels, metadata, index, state_id):
    price = float(data.iloc[index].close)
    upper, lower = _nearest_two_sided(levels, metadata, index, price)
    if upper is None or lower is None:
        return DestinationLabel(state_id, upper.level_id if upper else None, lower.level_id if lower else None, upper.price if upper else None, lower.price if lower else None, "UNRESOLVED_MISSING_SIDE", None, None)
    end = min(len(data) - 1, index + MAX_HOLD_MINUTES)
    for position in range(index + 1, end + 1):
        row = data.iloc[position]
        up, down = float(row.high) >= upper.price, float(row.low) <= lower.price
        if up and down:
            label = "AMBIGUOUS_SAME_MINUTE"
        elif up:
            label = "UPPER_FIRST"
        elif down:
            label = "LOWER_FIRST"
        else:
            continue
        return DestinationLabel(state_id, upper.level_id, lower.level_id, upper.price, lower.price, label, position, int(data.index[position].value))
    return DestinationLabel(state_id, upper.level_id, lower.level_id, upper.price, lower.price, "UNRESOLVED_HORIZON", None, None)


def _raw_economics(side: str, entry: float, stop_fill: float, target: float, entry_fee: float) -> tuple[float, float, float]:
    sign = _sign(side)
    risk = abs(entry - stop_fill)
    target_raw = sign * (target - entry) / risk - (entry_fee * abs(entry) + MAKER_FEE * abs(target)) / risk
    stop_raw = sign * (stop_fill - entry) / risk - (entry_fee * abs(entry) + TAKER_FEE * abs(stop_fill)) / risk
    normalization = max(abs(stop_raw), EPS)
    return target_raw / normalization, -1.0, normalization


def _resolve_after_fill(data, action, tick, fill_index, actual_entry, entry_fee):
    sign = _sign(action.side)
    stop_fill = float(action.stop) - sign * STOP_SLIPPAGE_TICKS * tick
    target_r, stop_r, normalization = _raw_economics(action.side, actual_entry, stop_fill, float(action.target), entry_fee)
    cash_risk = abs(actual_entry - stop_fill)
    planned_risk = abs(actual_entry - float(action.stop))
    actual_gross_rr = abs(float(action.target) - actual_entry) / max(planned_risk, EPS)
    if actual_gross_rr < 1.0:
        timestamp = int(data.index[fill_index].value)
        return ExecutionLabel("CANCELED_AT_FILL_RR_BELOW_ONE", "UNFILLED", None, None, None, None, float(fill_index - action.emission_index), None, timestamp, actual_entry, None, None, actual_gross_rr, None, None, None)
    best, worst = 0.0, 0.0
    end = min(len(data) - 1, fill_index + MAX_HOLD_MINUTES)
    for position in range(fill_index, end + 1):
        row = data.iloc[position]
        if action.side == "LONG":
            target_hit, stop_hit = float(row.high) >= float(action.target), float(row.low) <= float(action.stop)
            favorable = (float(row.high) - actual_entry) / cash_risk / normalization
            adverse = (float(row.low) - actual_entry) / cash_risk / normalization
        else:
            target_hit, stop_hit = float(row.low) <= float(action.target), float(row.high) >= float(action.stop)
            favorable = (actual_entry - float(row.low)) / cash_risk / normalization
            adverse = (actual_entry - float(row.high)) / cash_risk / normalization
        best, worst = max(best, favorable), min(worst, adverse)
        if target_hit and stop_hit:
            outcome, result = "AMBIGUOUS_SAME_MINUTE", stop_r
        elif stop_hit:
            outcome, result = "STOP_FIRST", stop_r
        elif target_hit:
            outcome, result = "TARGET_FIRST", target_r
        else:
            continue
        timestamp = int(data.index[position].value)
        return ExecutionLabel(action.entry_style == "MARKET" and "FILLED_MARKET_NEXT_OPEN" or "FILLED_LIMIT", outcome, fill_index, int(data.index[fill_index].value), position, timestamp, float(fill_index - action.emission_index), float(position - fill_index), timestamp, actual_entry, target_r, stop_r, actual_gross_rr, result, best, worst)
    exit_price = float(data.iloc[end].close) - sign * STOP_SLIPPAGE_TICKS * tick
    raw_exit = sign * (exit_price - actual_entry) / cash_risk - (entry_fee * abs(actual_entry) + TAKER_FEE * abs(exit_price)) / cash_risk
    result = raw_exit / normalization
    timestamp = int(data.index[end].value)
    return ExecutionLabel(action.entry_style == "MARKET" and "FILLED_MARKET_NEXT_OPEN" or "FILLED_LIMIT", "TIME_EXIT", fill_index, int(data.index[fill_index].value), end, timestamp, float(fill_index - action.emission_index), float(end - fill_index), timestamp, actual_entry, target_r, stop_r, actual_gross_rr, result, best, worst)


def label_action(data: pd.DataFrame, action: ActionSpec, tick: float) -> ExecutionLabel:
    start = int(action.emission_index) + 1
    if start >= len(data):
        return ExecutionLabel("NO_FUTURE", "UNRESOLVED", None, None, None, None, None, None, None, None, None, None, None, None, None, None)
    sign = _sign(action.side)
    if action.entry_style == "MARKET":
        entry = float(data.iloc[start].open) + sign * ENTRY_SLIPPAGE_TICKS * tick
        stop_fill = float(action.stop) - sign * STOP_SLIPPAGE_TICKS * tick
        valid = stop_fill < entry < action.target if action.side == "LONG" else action.target < entry < stop_fill
        if not valid:
            timestamp = int(data.index[start].value)
            return ExecutionLabel("CANCELED_AT_FILL_INVALID_GEOMETRY", "UNFILLED", None, None, None, None, 1.0, None, timestamp, entry, None, None, None, None, None, None)
        return _resolve_after_fill(data, action, tick, start, entry, TAKER_FEE)

    expiry = min(len(data) - 1, int(action.emission_index) + int(action.entry_expiry_minutes))
    for position in range(start, expiry + 1):
        row = data.iloc[position]
        invalidated = float(row.low) <= action.stop if action.side == "LONG" else float(row.high) >= action.stop
        target_spent = float(row.high) >= action.target if action.side == "LONG" else float(row.low) <= action.target
        traded_through = float(row.low) <= action.entry - LIMIT_TRADE_THROUGH_TICKS * tick if action.side == "LONG" else float(row.high) >= action.entry + LIMIT_TRADE_THROUGH_TICKS * tick
        if not traded_through:
            if invalidated or target_spent:
                timestamp = int(data.index[position].value)
                state = "CANCELED_PRE_FILL_INVALIDATION" if invalidated else "CANCELED_PRE_FILL_TARGET_SPENT"
                return ExecutionLabel(state, "UNFILLED", None, None, None, None, None, None, timestamp, None, None, None, None, None, None, None)
            continue
        if invalidated or target_spent:
            timestamp = int(data.index[position].value)
            # One-minute data cannot order the maker fill and barrier print.  Treat
            # the selected action conservatively as a full stop rather than crediting
            # a same-bar target.
            stop_fill = float(action.stop) - sign * STOP_SLIPPAGE_TICKS * tick
            target_r, stop_r, _ = _raw_economics(action.side, float(action.entry), stop_fill, float(action.target), MAKER_FEE)
            return ExecutionLabel("FILLED_LIMIT", "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE", position, timestamp, position, timestamp, float(position - action.emission_index), 0.0, timestamp, float(action.entry), target_r, stop_r, abs(action.target-action.entry)/max(abs(action.entry-action.stop),EPS), stop_r, 0.0, stop_r)
        return _resolve_after_fill(data, action, tick, position, float(action.entry), MAKER_FEE)
    timestamp = int(data.index[expiry].value)
    return ExecutionLabel("EXPIRED_UNFILLED", "UNFILLED", None, None, None, None, None, None, timestamp, None, None, None, None, None, None, None)


def _entry_variants(data, setup, response, event_meta, source, tick):
    side = setup.side
    decision = float(data.iloc[int(response["response_index"])].close)
    output = [("CONFIRMED_MARKET", "MARKET", decision)]
    proximal = float(setup.upper if side == "LONG" else setup.lower)
    midpoint = float(0.5 * (setup.lower + setup.upper))
    for name, price in (("ZONE_PROXIMAL_LIMIT", proximal), ("ZONE_MID_LIMIT", midpoint)):
        favorable = price <= decision - tick if side == "LONG" else price >= decision + tick
        if favorable:
            output.append((name, "LIMIT", price))
    deduped = []
    for item in output:
        if any(item[1] == prior[1] and abs(item[2] - prior[2]) <= tick for prior in deduped):
            continue
        deduped.append(item)
    return deduped


def _stop_variants(data, setup, response, source, event_meta, tick):
    branch = str(event_meta["narrative_branch"])
    parent = core._action_stop(setup, response, source, data, tick, branch)
    output = [("PARENT_EVENT_INVALIDATION" if branch == "FAILED_AUCTION_REVERSAL" else "TRANSFERRED_RETEST_INVALIDATION", parent)]
    if branch == "FAILED_AUCTION_REVERSAL":
        buffer = max(2.0 * tick, 0.05 * core._atr_price(data, int(response["response_index"])))
        reference = float(response["retest_extreme"])
        tighter = reference - buffer if setup.side == "LONG" else reference + buffer
        valid = tighter < float(data.iloc[int(response["response_index"])].close) if setup.side == "LONG" else tighter > float(data.iloc[int(response["response_index"])].close)
        if valid and abs(tighter - parent) > tick:
            output.append(("FIRST_RETEST_INVALIDATION", tighter))
    return output


def _common_features(data, levels, metadata, source, setup, response, event_meta, obstacle, route_features, entry, stop):
    emission = int(response["response_index"])
    source_meta, target_meta = metadata[source.level_id], metadata.get(obstacle.source_level_id) if obstacle.source_level_id else None
    economics = _economics(side=setup.side, entry=entry, stop=stop, target=obstacle.order_price, tick_size=CONTRACTS[source.symbol].tick_size, entry_style="MARKET" if abs(entry-float(data.iloc[emission].close))<1e-12 else "LIMIT")
    planned_target = economics["target_net_r"] / max(abs(economics["stop_net_r"]), EPS)
    features = {
        **economics,
        "planned_account_target_r": planned_target,
        "planned_account_stop_r": -1.0,
        **core._liquidity_map_features(data, levels, emission),
        **v3._semantic_map_features(data, levels, metadata, emission),
        **core._active_structure_features(data, levels, emission),
        **core._approach_features(data, setup.interaction_index, source),
        **core._row_state_features(data, setup.interaction_index, setup.side, "event"),
        **core._row_state_features(data, setup.confirmation_index, setup.side, "confirmation"),
        **core._row_state_features(data, emission, setup.side, "decision"),
        **core._clock_features(pd.Timestamp(data.index[emission])),
        **rich._anchored_vwap_features(data, setup.interaction_index, emission, setup.side),
        **rich._sequence_features(data, emission, setup.side),
        **rich._source_accumulation_features(data, source, setup.interaction_index),
        **rich._volume_route_features(data, emission, entry, obstacle.order_price),
        **route_features,
        **{key: value for key, value in response.items() if key not in {"departure_index","touch_index","response_index","retest_extreme","response_kind"}},
        "narrative_branch": event_meta["narrative_branch"],
        "setup_kind": setup.setup_kind,
        "location_kind": event_meta["location_kind"],
        "response_kind": response["response_kind"],
        "source_pool_kind": source_meta.pool_kind,
        "source_pool_members": float(source_meta.member_count),
        "source_pool_accumulated": float(source_meta.accumulated),
        "source_semantic_weight": float(source_meta.semantic_weight),
        "source_scale_minutes": float(source.timeframe_minutes),
        "source_strength_ratio": _finite(source.strength_ratio),
        "source_defense_count": float(source.defense_count),
        "source_age_minutes": float(emission-source.observed_index_1m),
        "target_pool_kind": target_meta.pool_kind if target_meta else obstacle.kind,
        "target_pool_members": float(target_meta.member_count) if target_meta else 0.0,
        "target_pool_accumulated": float(target_meta.accumulated) if target_meta else 0.0,
        "target_semantic_weight": float(target_meta.semantic_weight) if target_meta else float(obstacle.strength),
        "target_scale_minutes": float(obstacle.timeframe_minutes),
        "target_strength_ratio": float(obstacle.strength),
        "event_penetration_bps": abs(setup.event_extreme-source.price)/max(abs(source.price),EPS)*10000.0,
        "event_to_confirmation_minutes": float(setup.confirmation_index-setup.interaction_index),
        "zone_width_bps": (setup.upper-setup.lower)/max(abs(entry),EPS)*10000.0,
        "directional_gap_body_ratio": setup.directional_gap.middle_body_ratio,
        "directional_gap_range_ratio": setup.directional_gap.middle_range_ratio,
        "directional_gap_activity_ratio": setup.directional_gap.middle_activity_ratio,
        "directional_gap_delta_signed": setup.directional_gap.middle_delta_signed,
        "order_block_present": float(event_meta.get("order_block_index",-1.0)>=0.0),
        "diagnostic_event_time_ns": int(data.index[setup.interaction_index].value),
        "diagnostic_confirmation_time_ns": int(data.index[setup.confirmation_index].value),
        "diagnostic_departure_time_ns": int(data.index[int(response["departure_index"])].value),
        "diagnostic_first_return_time_ns": int(data.index[int(response["touch_index"])].value),
        "diagnostic_response_time_ns": int(data.index[emission].value),
        "diagnostic_source_lower": source.lower,
        "diagnostic_source_upper": source.upper,
        "diagnostic_zone_lower": setup.lower,
        "diagnostic_zone_upper": setup.upper,
        "diagnostic_event_extreme": setup.event_extreme,
        "diagnostic_retest_extreme": response["retest_extreme"],
        "diagnostic_target_level_id": obstacle.obstacle_id,
        "diagnostic_target_structure_price": obstacle.structure_price,
    }
    return features


def _make_actions(symbol, data, levels, metadata, source, setup, event_meta, response, tick):
    emission = int(response["response_index"])
    decision = float(data.iloc[emission].close)
    obstacle, route_features = _first_obstacle(data, levels, metadata, emission, decision, setup.side, tick)
    if obstacle is None:
        return [], None
    event_ns = int(data.index[setup.interaction_index].value)
    state_id = f"CAS4STATE:{symbol}:{event_ns}:{event_meta['narrative_branch']}:{_stable_id(source.level_id,setup.setup_kind)}"
    episode_id = f"CAS4:{symbol}:{event_ns}:{_stable_id(source.level_id)}"
    destination = _destination_label(data, levels, metadata, emission, state_id)
    actions=[]
    for entry_name, order_type, entry in _entry_variants(data, setup, response, event_meta, source, tick):
        for stop_name, stop in _stop_variants(data, setup, response, source, event_meta, tick):
            valid = stop < entry < obstacle.order_price if setup.side=="LONG" else obstacle.order_price < entry < stop
            if not valid:
                continue
            economics = _economics(side=setup.side,entry=entry,stop=stop,target=obstacle.order_price,tick_size=tick,entry_style=order_type)
            if not economics or economics["gross_rr"]<1.0 or economics["target_net_r"]<=0.0 or economics["stop_net_r"]>=0.0:
                continue
            features=_common_features(data,levels,metadata,source,setup,response,event_meta,obstacle,route_features,entry,stop)
            features.update({"state_id":state_id,"entry_geometry":entry_name,"stop_geometry":stop_name})
            action_id=f"{episode_id}:{event_meta['narrative_branch']}:{setup.setup_kind}:{event_meta['location_kind']}:{response['response_kind']}:{entry_name}:{stop_name}:{obstacle.kind}"
            action=ActionSpec(action_id=action_id,episode_id=episode_id,symbol=symbol,event_type=str(event_meta['narrative_branch']),decision_stage=f"{setup.setup_kind}_FIRST_RETURN_RESPONSE",side=setup.side,emission_index=emission,emission_time_ns=int(data.index[emission].value),entry_style=order_type,entry=float(entry),stop=float(stop),target=float(obstacle.order_price),entry_expiry_minutes=1 if order_type=="MARKET" else LIMIT_EXPIRY_MINUTES,source_level_id=source.level_id,source_kind=source.source_kind,source_timeframe_minutes=source.timeframe_minutes,source_span=source.span,source_price=source.price,source_lower=source.lower,source_upper=source.upper,source_strength_ratio=source.strength_ratio,source_defense_count=source.defense_count,source_age_minutes=float(emission-source.observed_index_1m),objective_id=obstacle.obstacle_id,objective_kind=obstacle.kind,objective_timeframe_minutes=obstacle.timeframe_minutes,objective_strength_ratio=obstacle.strength,interaction_time_ns=event_ns,feature_values=features)
            actions.append(action)
    return actions,destination


def generate_symbol(symbol,data,levels,metadata,trading_start):
    tick=CONTRACTS[symbol].tick_size;start_ns=int(pd.Timestamp(trading_start,tz="UTC").value)
    sources=sorted(direction_sources(levels,metadata),key=lambda level:(int(level.first_penetration_index),level.side,-metadata[level.level_id].semantic_weight,level.level_id))
    records=[];states=[];active_until={"HIGH":-1,"LOW":-1};seen=set();counts={"semantic_sources":len(sources),"source_interactions":0,"owned_events":0,"executable_actions":0}
    for source in sources:
        interaction=int(source.first_penetration_index)
        if interaction>=len(data) or int(data.index[interaction].value)<start_ns or interaction<=active_until[source.side]:continue
        clock=(interaction,source.side)
        if clock in seen:continue
        peers=[level for level in sources if level.side==source.side and int(level.first_penetration_index)==interaction]
        owner=max(peers,key=lambda level:(metadata[level.level_id].semantic_weight,level.timeframe_minutes,level.defense_count,level.strength_ratio));seen.add(clock)
        if owner.level_id!=source.level_id:continue
        counts["source_interactions"]+=1;candidates=v3._event_candidates(data,owner,tick)
        if not candidates:continue
        _,response_index,setup,event_meta,response=candidates[0];active_until[source.side]=max(active_until[source.side],response_index);counts["owned_events"]+=1
        actions,destination=_make_actions(symbol,data,levels,metadata,owner,setup,event_meta,response,tick)
        if not actions or destination is None:continue
        states.append({"state_id":destination.state_id,"symbol":symbol,"episode_id":actions[0].episode_id,"emission_index":actions[0].emission_index,"emission_time_ns":actions[0].emission_time_ns,"action_side":actions[0].side,**actions[0].feature_values,**asdict(destination)})
        for action in actions:
            label=label_action(data,action,tick);records.append({**{key:value for key,value in asdict(action).items() if key!="feature_values"},**action.feature_values,**asdict(label)});counts["executable_actions"]+=1
    frame=pd.DataFrame(records);state_frame=pd.DataFrame(states)
    if not frame.empty and frame.action_id.duplicated().any():raise RuntimeError(f"duplicate v4 action {symbol}")
    if not state_frame.empty:state_frame=state_frame.drop_duplicates("state_id",keep="first").reset_index(drop=True)
    summary={"symbol":symbol,"bars":len(data),"semantic_levels":len(levels),**counts,"outcomes":frame.outcome.value_counts().to_dict() if not frame.empty else {},"entries":frame.entry_geometry.value_counts().to_dict() if not frame.empty else {},"branches":frame.narrative_branch.value_counts().to_dict() if not frame.empty else {}}
    return frame,state_frame,summary


def run_research(*,start:date,end:date,warmup_days:int,symbols:Sequence[str],cache:Path,output:Path):
    from data_re1_flow import load_range_flow
    output.mkdir(parents=True,exist_ok=True);cache.mkdir(parents=True,exist_ok=True);load_start=start-timedelta(days=warmup_days)
    prepared={};raws={};levels_by={};meta_by={}
    for symbol in symbols:
        tick=CONTRACTS[symbol].tick_size;raw=load_range_flow(symbol,load_start,end,cache);index_price=load_reference_range("indexPriceKlines",symbol,load_start,end,cache);mark_price=load_reference_range("markPriceKlines",symbol,load_start,end,cache);metrics=load_range_metrics(symbol,load_start,end,cache);state=prepare_market_state(raw,index_price,mark_price,metrics,tick);levels,metadata=build_semantic_liquidity(symbol,state,raw,tick);prepared[symbol]=state;raws[symbol]=raw;levels_by[symbol]=levels;meta_by[symbol]=metadata
    prepared=_add_common_state(prepared);action_frames=[];state_frames=[];by_symbol={}
    for symbol in symbols:
        actions,states,summary=generate_symbol(symbol,prepared[symbol],levels_by[symbol],meta_by[symbol],start);by_symbol[symbol]=summary
        if not actions.empty:actions.to_csv(output/f"{symbol}_coherent_actions.csv",index=False);action_frames.append(actions)
        if not states.empty:states.to_csv(output/f"{symbol}_destination_states.csv",index=False);state_frames.append(states)
    actions=pd.concat(action_frames,ignore_index=True,sort=False) if action_frames else pd.DataFrame();states=pd.concat(state_frames,ignore_index=True,sort=False) if state_frames else pd.DataFrame();actions.to_csv(output/"coherent_actions.csv",index=False);states.to_csv(output/"destination_states.csv",index=False)
    resolved=actions[actions.outcome.isin(["TARGET_FIRST","STOP_FIRST","AMBIGUOUS_SAME_MINUTE","AMBIGUOUS_FILL_BARRIER_SAME_MINUTE","TIME_EXIT"])] if not actions.empty else actions
    summary={"start":start.isoformat(),"end":end.isoformat(),"warmup_days":warmup_days,"symbols":list(symbols),"actions":len(actions),"destination_states":len(states),"resolved_actions":len(resolved),"wins":int((resolved.outcome=="TARGET_FIRST").sum()) if not resolved.empty else 0,"win_rate":float((resolved.outcome=="TARGET_FIRST").mean()) if not resolved.empty else None,"mean_account_r":float(pd.to_numeric(resolved.net_r,errors="coerce").mean()) if not resolved.empty else None,"by_symbol":by_symbol,"policy":POLICY,"future_information_in_features":False,"future_information_in_labels_only":True}
    (output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8");return summary


__all__=["POLICY","run_research","generate_symbol","label_action"]
