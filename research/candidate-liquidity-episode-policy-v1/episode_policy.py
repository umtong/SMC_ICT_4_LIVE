#!/usr/bin/env python3
"""Causal liquidity episode policy.

The mature liquidity-world-model generator remains responsible for point-in-time
source detection, episode formation and destination-first plan geometry. This
module adds decision-time market context, requires dynamic trend-line/channel
liquidity, and removes the synthetic ATR entry fallback while preserving the
one-episode/one-plan contract.
"""
from __future__ import annotations

from typing import Any, Sequence

import pandas as pd

import dynamic_boundaries
import plan_geometry as geometry
import world_model_policy as base_policy
from episode_policy_features import FEATURE_COLUMNS, enrich_episode_frame
from world_model_common import Destination, sign

_BASE_PLAN_FROM_SIGNAL = base_policy.plan_from_signal
_BASE_CHOOSE_DESTINATION = geometry.choose_destination


def _choose_destination_with_dynamic(
    data,
    levels,
    metadata,
    nodes_by_scale,
    decision,
    entry,
    side,
    atr,
    tick,
):
    """Select nearest still-live opposing liquidity, including channel edges."""
    candidates: list[Destination] = []
    static = _BASE_CHOOSE_DESTINATION(
        data,
        levels,
        metadata,
        nodes_by_scale,
        decision,
        entry,
        side,
        atr,
        tick,
    )
    if static is not None:
        candidates.append(static)

    target_side = "HIGH" if side == "LONG" else "LOW"
    for model, price in dynamic_boundaries.active_route_boundaries(
        str(data.attrs.get("symbol", "")),
        data,
        decision,
        entry,
        side,
        tick,
    ):
        width = max(
            2.0 * tick,
            1.5 * float(model.residual_price),
            0.04 * float(atr[decision]),
        )
        candidates.append(
            Destination(
                destination_id=f"DYNAMIC_ROUTE:{model.boundary_id}:{decision}",
                side=target_side,
                lower=float(price - width),
                upper=float(price + width),
                price=float(price),
                observed_index=int(model.observed_index),
                scale=float(model.timeframe_minutes),
                strength=float(
                    max(model.quality, 0.0)
                    * (1.0 + 0.60 * max(model.channel_quality, 0.0))
                ),
                kind=(
                    f"DYNAMIC_CHANNEL_ROUTE_{model.timeframe_minutes}M"
                    if model.is_channel_edge
                    else f"DYNAMIC_TRENDLINE_ROUTE_{model.timeframe_minutes}M"
                ),
            )
        )

    if not candidates:
        return None
    direction = sign(side)
    ahead = [
        item
        for item in candidates
        if direction * (item.price - entry) > tick
    ]
    if not ahead:
        return None
    ahead.sort(
        key=lambda item: (
            direction * (item.price - entry),
            -item.strength,
            -item.scale,
            item.destination_id,
        )
    )
    nearest = direction * (ahead[0].price - entry)
    near = [
        item
        for item in ahead
        if direction * (item.price - entry)
        <= nearest + 0.20 * float(atr[decision])
    ]
    return max(
        near,
        key=lambda item: (
            item.strength,
            item.scale,
            -abs(item.price - entry),
        ),
    )


def _strict_plan_from_signal(*args, **kwargs):
    """Reject plans without an actual OB/FVG/source-origin entry object."""
    plan, reason = _BASE_PLAN_FROM_SIGNAL(*args, **kwargs)
    if plan is not None and str(plan.get("entry_geometry")) == "CAUSAL_DEPARTURE_BAND":
        return None, "NO_CAUSAL_ENTRY_ORIGIN"
    return plan, reason


geometry.choose_destination = _choose_destination_with_dynamic
base_policy.plan_from_signal = _strict_plan_from_signal


def _order_mask(frame: pd.DataFrame) -> pd.Series:
    return (
        frame.get("order_exists", pd.Series(False, index=frame.index))
        .astype(str)
        .str.lower()
        .isin({"true", "1", "yes"})
    )


def _assert_policy_invariants(frame: pd.DataFrame) -> None:
    orders = frame[_order_mask(frame)].copy()
    if orders.empty:
        return
    if "episode_id" not in orders:
        raise RuntimeError("Episode policy output is missing episode_id")
    duplicate = orders.episode_id.astype(str).duplicated(keep=False)
    if duplicate.any():
        examples = orders.loc[duplicate, "episode_id"].astype(str).head(10).tolist()
        raise RuntimeError(f"One-plan-per-episode invariant violated: {examples}")
    if orders.get("entry_geometry", pd.Series(index=orders.index, dtype=object)).astype(str).eq(
        "CAUSAL_DEPARTURE_BAND"
    ).any():
        raise RuntimeError("Synthetic entry fallback leaked into executable orders")
    gross_rr = pd.to_numeric(orders.get("gross_rr"), errors="coerce")
    if gross_rr.isna().any() or gross_rr.lt(1.0 - 1e-12).any():
        raise RuntimeError("Executable order violates the gross planned RR >= 1.0 contract")


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    trading_start: Any,
) -> tuple[pd.DataFrame, dict[str, int]]:
    combined_levels = list(levels)
    combined_metadata = dict(metadata)
    data.attrs["symbol"] = symbol

    tick = base_policy.core.CONTRACTS[symbol].tick_size
    models = dynamic_boundaries.build_dynamic_boundaries(
        symbol,
        data,
        combined_levels,
        tick,
    )
    dynamic_levels, dynamic_metadata = dynamic_boundaries.source_levels(
        symbol,
        data,
        models,
        tick,
        combined_levels,
    )
    combined_levels.extend(dynamic_levels)
    combined_metadata.update(dynamic_metadata)
    dynamic_count = len(dynamic_levels)
    dynamic_channel_count = sum(
        "CHANNEL" in str(level.source_kind) for level in dynamic_levels
    )

    frame, counts = base_policy.generate_symbol(
        symbol,
        data,
        combined_levels,
        combined_metadata,
        trading_start,
    )
    if _order_mask(frame).any():
        enriched = enrich_episode_frame(frame, data)
    else:
        enriched = frame.copy()
        for column in FEATURE_COLUMNS:
            if column not in enriched:
                enriched[column] = 0.0
        enriched["episode_policy_version"] = "liquidity-episode-policy-v1"

    _assert_policy_invariants(enriched)

    counts = dict(counts)
    counts["episode_policy_rows"] = int(len(enriched))
    counts["episode_policy_orders"] = int(_order_mask(enriched).sum())
    counts["uses_outcome_in_generation"] = 0
    counts["one_plan_per_episode"] = 1
    counts["synthetic_entry_fallback_enabled"] = 0
    counts["dynamic_liquidity_sources"] = int(dynamic_count)
    counts["dynamic_channel_sources"] = int(dynamic_channel_count)
    counts["dynamic_geometry_available"] = 1
    return enriched, counts


__all__ = ["generate_symbol"]
