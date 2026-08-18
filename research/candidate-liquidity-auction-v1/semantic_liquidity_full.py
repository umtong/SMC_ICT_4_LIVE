"""Causal liquidity ledger with separate direction-source and route-obstacle roles.

The coherent v4 ledger intentionally discarded weak 5-minute structures.  That is
reasonable for deciding direction, but it also removed nearby reaction points from the
exit map and left many plans with implausibly distant targets.  This extension keeps the
same strong semantic sources for direction while retaining every causally confirmed
level as a possible first route obstacle.  Weak local structure cannot create an event;
it can only stop an already valid event's route.
"""
from __future__ import annotations

from typing import Sequence

import hierarchical_liquidity_bpr as hl
import hierarchical_liquidity_bpr_v2 as hl2
import semantic_liquidity_v4 as base

PoolMeta = base.PoolMeta


def build_semantic_liquidity(symbol, data, raw, tick_size):
    levels, metadata = base.build_semantic_liquidity(symbol, data, raw, tick_size)
    existing = {level.level_id for level in levels}
    broad = hl2.detect_levels_v2(symbol, data, raw, tick_size)
    for level in broad:
        if level.level_id in existing:
            continue
        direction, _, kind = base._single_semantics(level)
        metadata[level.level_id] = PoolMeta(
            pool_kind=f"LOCAL_REACTION::{kind}",
            member_count=1,
            member_timeframes=str(level.timeframe_minutes),
            accumulated=False,
            direction_source=direction,
            route_obstacle=True,
            semantic_weight=base._weight(level),
        )
        levels.append(level)
        existing.add(level.level_id)
    levels.sort(
        key=lambda item: (
            item.observed_index_1m,
            item.side,
            -metadata[item.level_id].semantic_weight,
            item.level_id,
        )
    )
    return levels, metadata


def direction_sources(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
) -> list[hl.LiquidityLevel]:
    return [
        level
        for level in levels
        if metadata[level.level_id].direction_source
        and level.first_penetration_index is not None
    ]


def route_levels(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
) -> list[hl.LiquidityLevel]:
    return [level for level in levels if metadata[level.level_id].route_obstacle]


__all__ = [
    "PoolMeta",
    "build_semantic_liquidity",
    "direction_sources",
    "route_levels",
]
