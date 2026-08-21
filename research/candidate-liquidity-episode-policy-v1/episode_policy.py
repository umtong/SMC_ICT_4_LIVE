#!/usr/bin/env python3
"""Restored causal liquidity episode policy.

The mature liquidity-world-model generator remains responsible for point-in-time
source detection, episode formation and destination-first plan geometry. This
module adds a decision-time market-context representation without changing the
one-episode/one-plan contract.
"""
from __future__ import annotations

from typing import Any, Sequence

import pandas as pd

import world_model_policy as base_policy
from episode_policy_features import FEATURE_COLUMNS, enrich_episode_frame

try:
    import dynamic_boundaries
except Exception:  # The workflow records whether dynamic geometry was available.
    dynamic_boundaries = None


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    trading_start: Any,
) -> tuple[pd.DataFrame, dict[str, int]]:
    combined_levels = list(levels)
    combined_metadata = dict(metadata)
    dynamic_count = 0
    dynamic_channel_count = 0
    if dynamic_boundaries is not None:
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
    has_orders = (
        frame.get("order_exists", pd.Series(False, index=frame.index))
        .astype(str)
        .str.lower()
        .isin({"true", "1", "yes"})
        .any()
    )
    if has_orders:
        enriched = enrich_episode_frame(frame, data)
    else:
        enriched = frame.copy()
        for column in FEATURE_COLUMNS:
            if column not in enriched:
                enriched[column] = 0.0
        enriched["episode_policy_version"] = "liquidity-episode-policy-v1"

    counts = dict(counts)
    counts["episode_policy_rows"] = int(len(enriched))
    counts["episode_policy_orders"] = int(
        enriched.get("order_exists", pd.Series(dtype=bool))
        .astype(str)
        .str.lower()
        .isin({"true", "1", "yes"})
        .sum()
    )
    counts["uses_outcome_in_generation"] = 0
    counts["one_plan_per_episode"] = 1
    counts["dynamic_liquidity_sources"] = int(dynamic_count)
    counts["dynamic_channel_sources"] = int(dynamic_channel_count)
    counts["dynamic_geometry_available"] = int(dynamic_boundaries is not None)
    return enriched, counts


__all__ = ["generate_symbol"]
