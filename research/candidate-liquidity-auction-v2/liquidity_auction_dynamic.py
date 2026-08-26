"""Unified horizontal and diagonal liquidity-auction policy.

The v1 policy repairs plan geometry at horizontal liquidity.  This extension adds the
missing structural language used throughout the EasyChart material and public trades:
confirmed wick trend lines and parallel channel edges become moving public-liquidity
boundaries, not independent tool strategies.  Their first penetration is interpreted by
the same failed-versus-accepted auction state machine, and an unconsumed active diagonal
boundary may be the nearest honest route obstacle for any valid episode.
"""
from __future__ import annotations

from typing import Any, Sequence
import math

import numpy as np
import pandas as pd

import coherent_system_v4 as v4
import hierarchical_liquidity_bpr as hl
import liquidity_auction_system as horizontal
from dynamic_boundaries import (
    active_route_boundaries,
    boundary_for_source,
    build_dynamic_boundaries,
    source_levels,
)
from semantic_liquidity_full import PoolMeta


POLICY = (
    "HORIZONTAL_AND_CAUSAL_DYNAMIC_DIAGONAL_LIQUIDITY_MAP_THEN_MUTUALLY_"
    "EXCLUSIVE_FAILED_OR_ACCEPTED_AUCTION_THEN_PRICE_VOLUME_CONTROL_TRANSFER_"
    "THEN_MARKET_OR_PUBLIC_PROXIMAL_FIRST_RETURN_ENTRY_THEN_EVENT_PLUS_PRIOR_"
    "WICK_NOISE_INVALIDATION_THEN_NEAREST_STATIC_VOLUME_TRENDLINE_OR_CHANNEL_"
    "ROUTE_OBSTACLE"
)
EPS = 1e-12
_BASE_BUILD = horizontal.build_semantic_liquidity
_BASE_FIRST_OBSTACLE = horizontal._first_obstacle
_BASE_COMMON_FEATURES = horizontal._common_features


def build_semantic_liquidity(
    symbol: str,
    data: pd.DataFrame,
    raw: pd.DataFrame,
    tick: float,
) -> tuple[list[hl.LiquidityLevel], dict[str, PoolMeta]]:
    levels, metadata = _BASE_BUILD(symbol, data, raw, tick)
    models = build_dynamic_boundaries(symbol, data, levels, tick)
    moving_levels, moving_metadata = source_levels(
        symbol,
        data,
        models,
        tick,
        levels,
    )
    levels.extend(moving_levels)
    metadata.update(moving_metadata)
    levels.sort(
        key=lambda item: (
            int(item.observed_index_1m),
            item.side,
            int(item.first_penetration_index)
            if item.first_penetration_index is not None
            else len(data) + 1,
            -float(metadata[item.level_id].semantic_weight),
            item.level_id,
        )
    )
    return levels, metadata


def direction_sources(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
) -> list[hl.LiquidityLevel]:
    return horizontal.direction_sources(levels, metadata)


def route_levels(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
) -> list[hl.LiquidityLevel]:
    return horizontal.route_levels(levels, metadata)


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _first_obstacle(
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    index: int,
    entry: float,
    side: str,
    tick: float,
) -> tuple[v4.Obstacle | None, dict[str, float]]:
    base, features = _BASE_FIRST_OBSTACLE(
        data,
        levels,
        metadata,
        index,
        entry,
        side,
        tick,
    )
    symbol = levels[0].symbol if levels else ""
    moving = active_route_boundaries(
        symbol,
        data,
        index,
        entry,
        side,
        tick,
    )
    diagonal: v4.Obstacle | None = None
    diagonal_features: dict[str, float] = {
        "dynamic_route_present": 0.0,
        "dynamic_route_is_channel": 0.0,
        "dynamic_route_quality": 0.0,
        "dynamic_route_channel_quality": 0.0,
        "dynamic_route_slope_atr_per_hour": 0.0,
        "dynamic_route_anchor_count": 0.0,
        "dynamic_route_distance_bps": 0.0,
    }
    if moving:
        model, structure = moving[0]
        target = structure - _sign(side) * v4.TARGET_INSIDE_TICKS * tick
        kind = (
            f"DYNAMIC_CHANNEL_EDGE_{model.timeframe_minutes}M"
            if model.is_channel_edge
            else f"DYNAMIC_TRENDLINE_{model.timeframe_minutes}M"
        )
        diagonal = v4.Obstacle(
            obstacle_id=f"{model.boundary_id}:ROUTE:{index}",
            kind=kind,
            timeframe_minutes=model.timeframe_minutes,
            structure_price=float(structure),
            order_price=float(target),
            strength=float(model.quality + 0.5 * model.channel_quality),
            source_level_id=None,
        )
        diagonal_features = {
            "dynamic_route_present": 1.0,
            "dynamic_route_is_channel": float(model.is_channel_edge),
            "dynamic_route_quality": float(model.quality),
            "dynamic_route_channel_quality": float(model.channel_quality),
            "dynamic_route_slope_atr_per_hour": float(model.normalized_slope),
            "dynamic_route_anchor_count": float(model.anchor_count),
            "dynamic_route_distance_bps": abs(target - entry)
            / max(abs(entry), EPS)
            * 10_000.0,
        }
    candidates = [item for item in (base, diagonal) if item is not None]
    features.update(diagonal_features)
    if not candidates:
        return None, features
    chosen = min(
        candidates,
        key=lambda item: (
            abs(item.order_price - entry),
            -float(item.strength),
            item.obstacle_id,
        ),
    )
    features.update(
        {
            "route_obstacle_is_local_or_semantic": float(
                chosen.source_level_id is not None
            ),
            "route_obstacle_is_multiscale_volume": float(
                chosen.kind.startswith("CAUSAL_VOLUME_NODE_")
            ),
            "route_obstacle_is_dynamic_diagonal": float(
                chosen.kind.startswith("DYNAMIC_")
            ),
            "route_obstacle_distance_bps": abs(chosen.order_price - entry)
            / max(abs(entry), EPS)
            * 10_000.0,
            "route_obstacle_strength": float(chosen.strength),
        }
    )
    return chosen, features


def _common_features(*args: Any, **kwargs: Any) -> dict[str, Any]:
    features = _BASE_COMMON_FEATURES(*args, **kwargs)
    data: pd.DataFrame = args[0]
    source: hl.LiquidityLevel = args[3]
    response: dict[str, Any] = args[5]
    entry = float(args[9])
    model = boundary_for_source(source.level_id)
    if model is None:
        features.update(
            {
                "dynamic_source_present": 0.0,
                "dynamic_source_is_channel": 0.0,
                "dynamic_source_quality": 0.0,
                "dynamic_source_channel_quality": 0.0,
                "dynamic_source_slope_atr_per_hour": 0.0,
                "dynamic_source_anchor_count": 0.0,
                "dynamic_source_residual_bps": 0.0,
                "dynamic_source_drift_to_decision_bps": 0.0,
                "dynamic_source_decision_distance_bps": 0.0,
                "dynamic_source_channel_width_bps": 0.0,
            }
        )
        return features
    emission = int(response["response_index"])
    projected = float(model.value_at(emission))
    interaction_price = float(source.price)
    features.update(
        {
            "dynamic_source_present": 1.0,
            "dynamic_source_is_channel": float(model.is_channel_edge),
            "dynamic_source_quality": float(model.quality),
            "dynamic_source_channel_quality": float(model.channel_quality),
            "dynamic_source_slope_atr_per_hour": float(model.normalized_slope),
            "dynamic_source_anchor_count": float(model.anchor_count),
            "dynamic_source_residual_bps": float(model.residual_price)
            / max(abs(interaction_price), EPS)
            * 10_000.0,
            "dynamic_source_drift_to_decision_bps": abs(projected - interaction_price)
            / max(abs(interaction_price), EPS)
            * 10_000.0,
            "dynamic_source_decision_distance_bps": (
                float(data.iloc[emission].close) - projected
            )
            * (1.0 if source.side == "LOW" else -1.0)
            / max(abs(projected), EPS)
            * 10_000.0,
            "dynamic_source_channel_width_bps": float(
                model.channel_width_at_observation
            )
            / max(abs(entry), EPS)
            * 10_000.0,
        }
    )
    return features


# The reusable v4 event/action engine now consumes one unified horizontal/diagonal map.
v4.PoolMeta = PoolMeta
v4.build_semantic_liquidity = build_semantic_liquidity
v4.direction_sources = direction_sources
v4.route_levels = route_levels
v4._first_obstacle = _first_obstacle
v4._common_features = _common_features
v4.POLICY = POLICY

run_research = v4.run_research
generate_symbol = v4.generate_symbol
label_action = v4.label_action
MAX_HOLD_MINUTES = v4.MAX_HOLD_MINUTES
LIMIT_EXPIRY_MINUTES = v4.LIMIT_EXPIRY_MINUTES

__all__ = [
    "POLICY",
    "MAX_HOLD_MINUTES",
    "LIMIT_EXPIRY_MINUTES",
    "run_research",
    "generate_symbol",
    "label_action",
]
