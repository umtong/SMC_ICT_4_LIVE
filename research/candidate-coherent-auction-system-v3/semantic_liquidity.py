"""Causal semantic liquidity-pool ledger.

A confirmed pivot is evidence of structure, not automatically a tradable pool.  This
module converts the broad causal pivot inventory into the smaller set of liquidity
objects a skilled trader would plausibly use for direction and routing:

* previous completed day/week extremes;
* 60m+ external swings;
* 15m swings which are defended, prominent, or part of an equal-high/low cluster;
* cross-timeframe equal-high/equal-low clusters observed before their first raid;
* meaningful 5m clusters for the first route obstacle, never as lone direction owners.

Clustering is performed in observation order.  A pool becomes available only after the
last member used to define it has itself become observable.  Its first penetration is
then scanned strictly after that observation time.  Future path information never
changes the pool price, membership, or meaning.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
import hashlib
import math

import numpy as np
import pandas as pd

import hierarchical_liquidity_bpr as hl
import hierarchical_liquidity_bpr_v2 as hl2


@dataclass(frozen=True, slots=True)
class PoolMeta:
    pool_kind: str
    member_count: int
    member_timeframes: str
    accumulated: bool
    direction_source: bool
    route_obstacle: bool
    semantic_weight: float


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _stable(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]


def _zone_width(level: hl.LiquidityLevel) -> float:
    return max(float(level.upper - level.lower), abs(float(level.price)) * 1e-7, 1e-12)


def _clusterable(left: hl.LiquidityLevel, right: hl.LiquidityLevel) -> bool:
    if left.side != right.side:
        return False
    tolerance = 1.35 * max(_zone_width(left), _zone_width(right))
    return abs(float(left.price) - float(right.price)) <= tolerance


def _pool_weight(level: hl.LiquidityLevel) -> float:
    scale = math.sqrt(max(int(level.timeframe_minutes), 5) / 5.0)
    strength = max(0.2, _finite(level.strength_ratio, 0.2))
    defense = 1.0 + math.log1p(max(0, int(level.defense_count) - 1))
    return scale * strength * defense


def _derived_pool(
    symbol: str,
    data: pd.DataFrame,
    members: Sequence[hl.LiquidityLevel],
) -> hl.LiquidityLevel:
    side = members[0].side
    observed_member = max(members, key=lambda item: (item.observed_index_1m, item.observed_time_ns))
    weights = np.asarray([_pool_weight(item) for item in members], dtype=float)
    prices = np.asarray([float(item.price) for item in members], dtype=float)
    # The raid must clear the outer edge of the accumulated stops.
    price = float(np.max(prices) if side == "HIGH" else np.min(prices))
    lower = float(min(item.lower for item in members))
    upper = float(max(item.upper for item in members))
    timeframe = int(max(item.timeframe_minutes for item in members))
    strength = float(np.average([_finite(item.strength_ratio, 0.0) for item in members], weights=np.maximum(weights, 1e-9)))
    defense = int(sum(max(1, int(item.defense_count)) for item in members))
    observed_index = int(observed_member.observed_index_1m)
    observed_time = int(observed_member.observed_time_ns)
    event_time = int(min(item.event_time_ns for item in members))
    member_ids = tuple(sorted(item.level_id for item in members))
    level = hl.LiquidityLevel(
        level_id=f"{symbol}:SEMANTIC_POOL:{side}:{observed_time}:{_stable(*member_ids)}",
        symbol=symbol,
        side=side,
        timeframe_minutes=timeframe,
        span=0,
        price=price,
        lower=lower,
        upper=upper,
        event_time_ns=event_time,
        observed_time_ns=observed_time,
        observed_index_1m=observed_index,
        strength_ratio=strength,
        defense_count=defense,
        source_kind=f"ACCUMULATED_{side}_LIQUIDITY_POOL",
    )
    level.first_penetration_index = hl._first_penetration(data, level)
    return level


def _semantic_single(level: hl.LiquidityLevel) -> tuple[bool, bool, str]:
    kind = str(level.source_kind)
    period = "PREVIOUS_DAY" in kind or "PREVIOUS_WEEK" in kind
    timeframe = int(level.timeframe_minutes)
    strength = _finite(level.strength_ratio, 0.0)
    defenses = int(level.defense_count)
    direction_source = (
        period
        or timeframe >= 60
        or (timeframe >= 15 and (defenses >= 2 or strength >= 1.20))
    )
    route_obstacle = direction_source or defenses >= 2 or (timeframe >= 5 and strength >= 1.35)
    if period:
        pool_kind = "COMPLETED_PERIOD_EXTREME"
    elif timeframe >= 240:
        pool_kind = "MAJOR_EXTERNAL_SWING"
    elif timeframe >= 60:
        pool_kind = "EXTERNAL_SWING"
    elif direction_source:
        pool_kind = "DEFENDED_INTRADAY_SWING"
    else:
        pool_kind = "MINOR_STRUCTURE"
    return direction_source, route_obstacle, pool_kind


def build_semantic_liquidity(
    symbol: str,
    data: pd.DataFrame,
    raw: pd.DataFrame,
    tick_size: float,
) -> tuple[list[hl.LiquidityLevel], dict[str, PoolMeta]]:
    broad = hl2.detect_levels_v2(symbol, data, raw, tick_size)
    broad = sorted(
        broad,
        key=lambda item: (
            item.observed_index_1m,
            item.side,
            item.timeframe_minutes,
            item.level_id,
        ),
    )

    active_clusters: dict[str, list[list[hl.LiquidityLevel]]] = {"HIGH": [], "LOW": []}
    derived: list[hl.LiquidityLevel] = []
    used_cluster_ids: set[str] = set()
    for level in broad:
        side_clusters = active_clusters[level.side]
        compatible = [cluster for cluster in side_clusters if any(_clusterable(level, member) for member in cluster)]
        if compatible:
            cluster = max(
                compatible,
                key=lambda items: (
                    max(member.timeframe_minutes for member in items),
                    sum(member.defense_count for member in items),
                    len(items),
                ),
            )
            cluster.append(level)
        else:
            cluster = [level]
            side_clusters.append(cluster)
        # Keep recent unconsumed structural neighborhoods; old clusters remain as
        # immutable emitted pools but do not absorb unrelated levels forever.
        if len(cluster) >= 2:
            distinct_events = {member.event_time_ns for member in cluster}
            distinct_scales = {member.timeframe_minutes for member in cluster}
            if len(distinct_events) >= 2:
                pool = _derived_pool(symbol, data, cluster)
                if pool.level_id not in used_cluster_ids:
                    derived.append(pool)
                    used_cluster_ids.add(pool.level_id)
        if len(side_clusters) > 96:
            active_clusters[level.side] = side_clusters[-96:]

    levels: list[hl.LiquidityLevel] = []
    metadata: dict[str, PoolMeta] = {}
    for level in broad:
        direction_source, route_obstacle, kind = _semantic_single(level)
        if not route_obstacle:
            continue
        weight = _pool_weight(level)
        metadata[level.level_id] = PoolMeta(
            pool_kind=kind,
            member_count=1,
            member_timeframes=str(level.timeframe_minutes),
            accumulated=False,
            direction_source=direction_source,
            route_obstacle=route_obstacle,
            semantic_weight=weight,
        )
        levels.append(level)
    for pool in derived:
        member_scales = sorted(
            {
                member.timeframe_minutes
                for cluster in active_clusters[pool.side]
                for member in cluster
                if _clusterable(pool, member) and member.observed_index_1m <= pool.observed_index_1m
            }
        )
        direction_source = pool.timeframe_minutes >= 15 or pool.defense_count >= 2
        metadata[pool.level_id] = PoolMeta(
            pool_kind="ACCUMULATED_EQUAL_HIGH_LOW_POOL",
            member_count=max(2, int(pool.defense_count)),
            member_timeframes="|".join(str(item) for item in member_scales),
            accumulated=True,
            direction_source=direction_source,
            route_obstacle=True,
            semantic_weight=_pool_weight(pool) * (1.0 + math.log1p(max(1, pool.defense_count))),
        )
        levels.append(pool)

    # Prefer the semantically stronger representative where zones are essentially the
    # same.  This is decided at observation time and never from later trade outcomes.
    levels.sort(
        key=lambda item: (
            item.observed_index_1m,
            item.side,
            -metadata[item.level_id].semantic_weight,
            item.level_id,
        )
    )
    kept: list[hl.LiquidityLevel] = []
    for level in levels:
        duplicates = [
            prior for prior in kept
            if prior.side == level.side
            and abs(prior.observed_index_1m - level.observed_index_1m) <= 1
            and _clusterable(prior, level)
        ]
        if not duplicates:
            kept.append(level)
            continue
        best = max(
            [level, *duplicates],
            key=lambda item: (
                metadata[item.level_id].semantic_weight,
                item.timeframe_minutes,
                item.defense_count,
                item.strength_ratio,
            ),
        )
        if best is level:
            kept = [item for item in kept if item not in duplicates]
            kept.append(level)
    kept.sort(key=lambda item: (item.observed_index_1m, item.side, item.level_id))
    return kept, metadata


def direction_sources(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
) -> list[hl.LiquidityLevel]:
    return [
        level for level in levels
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
