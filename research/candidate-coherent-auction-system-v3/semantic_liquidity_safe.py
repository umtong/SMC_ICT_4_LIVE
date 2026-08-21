"""Compatibility-safe semantic pool construction.

The project has evolved the LiquidityLevel dataclass across research branches.  Build a
new semantic pool by replacing an existing level and only setting fields actually
present in the live dataclass, rather than duplicating a constructor contract.
"""
from __future__ import annotations

from dataclasses import fields, replace
from typing import Sequence
import math

import numpy as np
import pandas as pd

import hierarchical_liquidity_bpr as hl
import semantic_liquidity as semantic


def _derived_pool_safe(
    symbol: str,
    data: pd.DataFrame,
    members: Sequence[hl.LiquidityLevel],
) -> hl.LiquidityLevel:
    side = members[0].side
    observed_member = max(members, key=lambda item: (item.observed_index_1m, item.observed_time_ns))
    weights = np.asarray([semantic._pool_weight(item) for item in members], dtype=float)
    prices = np.asarray([float(item.price) for item in members], dtype=float)
    price = float(np.max(prices) if side == "HIGH" else np.min(prices))
    member_ids = tuple(sorted(item.level_id for item in members))
    updates = {
        "level_id": f"{symbol}:SEMANTIC_POOL:{side}:{int(observed_member.observed_time_ns)}:{semantic._stable(*member_ids)}",
        "symbol": symbol,
        "side": side,
        "timeframe_minutes": int(max(item.timeframe_minutes for item in members)),
        "span": 0,
        "price": price,
        "lower": float(min(item.lower for item in members)),
        "upper": float(max(item.upper for item in members)),
        "event_time_ns": int(min(item.event_time_ns for item in members)),
        "observed_time_ns": int(observed_member.observed_time_ns),
        "observed_index_1m": int(observed_member.observed_index_1m),
        "strength_ratio": float(
            np.average(
                [semantic._finite(item.strength_ratio, 0.0) for item in members],
                weights=np.maximum(weights, 1e-9),
            )
        ),
        "defense_count": int(sum(max(1, int(item.defense_count)) for item in members)),
        "source_kind": f"ACCUMULATED_{side}_LIQUIDITY_POOL",
        "first_penetration_index": None,
    }
    available = {field.name for field in fields(type(members[0]))}
    level = replace(members[0], **{key: value for key, value in updates.items() if key in available})
    penetration = hl._first_penetration(data, level)
    if "first_penetration_index" in available:
        level = replace(level, first_penetration_index=penetration)
    else:
        setattr(level, "first_penetration_index", penetration)
    return level


semantic._derived_pool = _derived_pool_safe

PoolMeta = semantic.PoolMeta
build_semantic_liquidity = semantic.build_semantic_liquidity
direction_sources = semantic.direction_sources
route_levels = semantic.route_levels

__all__ = ["PoolMeta", "build_semantic_liquidity", "direction_sources", "route_levels"]
