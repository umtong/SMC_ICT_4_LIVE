"""Unconsumed causal semantic liquidity ledger for the coherent v4 system."""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
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


def _width(level: hl.LiquidityLevel) -> float:
    return max(float(level.upper - level.lower), abs(float(level.price)) * 1e-7, 1e-12)


def _near(left: hl.LiquidityLevel, right: hl.LiquidityLevel) -> bool:
    return left.side == right.side and abs(float(left.price) - float(right.price)) <= 1.35 * max(_width(left), _width(right))


def _weight(level: hl.LiquidityLevel) -> float:
    scale = math.sqrt(max(int(level.timeframe_minutes), 5) / 5.0)
    strength = max(0.2, _finite(level.strength_ratio, 0.2))
    defense = 1.0 + math.log1p(max(0, int(level.defense_count) - 1))
    return scale * strength * defense


def _unconsumed_at(level: hl.LiquidityLevel, observed_index: int) -> bool:
    return level.first_penetration_index is None or int(level.first_penetration_index) > observed_index


def _replace_level(member: hl.LiquidityLevel, updates: dict[str, Any]) -> hl.LiquidityLevel:
    available = {field.name for field in fields(type(member))}
    return replace(member, **{key: value for key, value in updates.items() if key in available})


def _derive_pool(symbol: str, data: pd.DataFrame, members: Sequence[hl.LiquidityLevel]) -> hl.LiquidityLevel:
    side = members[0].side
    last = max(members, key=lambda item: (item.observed_index_1m, item.observed_time_ns))
    weights = np.asarray([_weight(item) for item in members], dtype=float)
    price = max(float(item.price) for item in members) if side == "HIGH" else min(float(item.price) for item in members)
    ids = tuple(sorted(item.level_id for item in members))
    updates = {
        "level_id": f"{symbol}:ACTIVE_POOL:{side}:{int(last.observed_time_ns)}:{_stable(*ids)}",
        "symbol": symbol,
        "side": side,
        "timeframe_minutes": int(max(item.timeframe_minutes for item in members)),
        "span": 0,
        "price": float(price),
        "lower": float(min(item.lower for item in members)),
        "upper": float(max(item.upper for item in members)),
        "event_time_ns": int(min(item.event_time_ns for item in members)),
        "observed_time_ns": int(last.observed_time_ns),
        "observed_index_1m": int(last.observed_index_1m),
        "strength_ratio": float(np.average([_finite(item.strength_ratio, 0.0) for item in members], weights=np.maximum(weights, 1e-9))),
        "defense_count": int(len(members)),
        "source_kind": f"ACCUMULATED_{side}_LIQUIDITY_POOL",
        "first_penetration_index": None,
    }
    level = _replace_level(members[0], updates)
    penetration = hl._first_penetration(data, level)
    level = _replace_level(level, {"first_penetration_index": penetration})
    if not hasattr(level, "first_penetration_index"):
        setattr(level, "first_penetration_index", penetration)
    return level


def _single_semantics(level: hl.LiquidityLevel) -> tuple[bool, bool, str]:
    kind = str(level.source_kind)
    period = "PREVIOUS_DAY" in kind or "PREVIOUS_WEEK" in kind
    timeframe = int(level.timeframe_minutes)
    strength = _finite(level.strength_ratio, 0.0)
    defenses = int(level.defense_count)
    direction = period or timeframe >= 60 or (timeframe >= 15 and (defenses >= 2 or strength >= 1.20))
    route = direction or defenses >= 2 or (timeframe >= 5 and strength >= 1.35)
    if period:
        semantic = "COMPLETED_PERIOD_EXTREME"
    elif timeframe >= 240:
        semantic = "MAJOR_EXTERNAL_SWING"
    elif timeframe >= 60:
        semantic = "EXTERNAL_SWING"
    elif direction:
        semantic = "DEFENDED_INTRADAY_SWING"
    else:
        semantic = "MINOR_ROUTE_STRUCTURE"
    return direction, route, semantic


def build_semantic_liquidity(
    symbol: str,
    data: pd.DataFrame,
    raw: pd.DataFrame,
    tick_size: float,
) -> tuple[list[hl.LiquidityLevel], dict[str, PoolMeta]]:
    broad = sorted(
        hl2.detect_levels_v2(symbol, data, raw, tick_size),
        key=lambda item: (item.observed_index_1m, item.side, item.timeframe_minutes, item.level_id),
    )
    clusters: dict[str, list[list[hl.LiquidityLevel]]] = {"HIGH": [], "LOW": []}
    emitted: dict[str, tuple[hl.LiquidityLevel, tuple[str, ...]]] = {}
    for level in broad:
        observed = int(level.observed_index_1m)
        valid_clusters = [
            cluster for cluster in clusters[level.side]
            if all(_unconsumed_at(member, observed) for member in cluster)
        ]
        compatible = [cluster for cluster in valid_clusters if any(_near(level, member) for member in cluster)]
        if compatible:
            cluster = max(compatible, key=lambda items: (max(item.timeframe_minutes for item in items), len(items), sum(item.defense_count for item in items)))
            cluster.append(level)
        else:
            cluster = [level]
            clusters[level.side].append(cluster)
        # Once a cluster member is consumed it cannot inflate a newly accumulated pool.
        clusters[level.side] = [
            items for items in clusters[level.side]
            if any(_unconsumed_at(member, observed) for member in items)
        ][-96:]
        distinct = {item.event_time_ns for item in cluster}
        if len(cluster) >= 2 and len(distinct) >= 2:
            pool = _derive_pool(symbol, data, tuple(cluster))
            ids = tuple(sorted(item.level_id for item in cluster))
            emitted[pool.level_id] = (pool, ids)

    levels: list[hl.LiquidityLevel] = []
    metadata: dict[str, PoolMeta] = {}
    for level in broad:
        direction, route, kind = _single_semantics(level)
        if not route:
            continue
        metadata[level.level_id] = PoolMeta(
            pool_kind=kind,
            member_count=1,
            member_timeframes=str(level.timeframe_minutes),
            accumulated=False,
            direction_source=direction,
            route_obstacle=route,
            semantic_weight=_weight(level),
        )
        levels.append(level)
    broad_by_id = {level.level_id: level for level in broad}
    for pool, ids in emitted.values():
        members = [broad_by_id[item] for item in ids if item in broad_by_id]
        metadata[pool.level_id] = PoolMeta(
            pool_kind="ACCUMULATED_EQUAL_HIGH_LOW_POOL",
            member_count=len(members),
            member_timeframes="|".join(str(value) for value in sorted({item.timeframe_minutes for item in members})),
            accumulated=True,
            direction_source=pool.timeframe_minutes >= 15 or len(members) >= 2,
            route_obstacle=True,
            semantic_weight=_weight(pool) * (1.0 + math.log1p(len(members))),
        )
        levels.append(pool)

    # Only exact-time/price duplicates are collapsed.  A later pool at the same price is
    # a new causal object because new stops can accumulate after the old pool is raided.
    levels.sort(key=lambda item: (item.observed_index_1m, item.side, -metadata[item.level_id].semantic_weight, item.level_id))
    kept: list[hl.LiquidityLevel] = []
    for level in levels:
        duplicates = [
            prior for prior in kept
            if prior.side == level.side
            and prior.observed_index_1m == level.observed_index_1m
            and _near(prior, level)
        ]
        if not duplicates:
            kept.append(level)
            continue
        best = max([level, *duplicates], key=lambda item: (metadata[item.level_id].semantic_weight, item.timeframe_minutes, item.defense_count))
        if best is level:
            kept = [item for item in kept if item not in duplicates]
            kept.append(level)
    kept.sort(key=lambda item: (item.observed_index_1m, item.side, item.level_id))
    return kept, metadata


def direction_sources(levels: Sequence[hl.LiquidityLevel], metadata: dict[str, PoolMeta]) -> list[hl.LiquidityLevel]:
    return [level for level in levels if metadata[level.level_id].direction_source and level.first_penetration_index is not None]


def route_levels(levels: Sequence[hl.LiquidityLevel], metadata: dict[str, PoolMeta]) -> list[hl.LiquidityLevel]:
    return [level for level in levels if metadata[level.level_id].route_obstacle]


__all__ = ["PoolMeta", "build_semantic_liquidity", "direction_sources", "route_levels"]
