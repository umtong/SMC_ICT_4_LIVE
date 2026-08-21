#!/usr/bin/env python3
"""Candidate 1a overlay for the inherited auction-episode harvester.

This module deliberately reuses the v3-v7/episode implementation.  It only repairs the
interface drift between the v6 generator and the v4 semantic-liquidity ledger, then
executes the complete inherited causal episode pipeline.
"""
from __future__ import annotations

from typing import Any

import auction_episode_harvest as episode

core = episode.core
_original_direction_sources = core.direction_sources


def _direction_sources_compat(
    levels: Any,
    metadata: Any,
    minimum_timeframe: int | float | None = None,
):
    sources = _original_direction_sources(levels, metadata)
    if minimum_timeframe is None or float(minimum_timeframe) <= 0.0:
        return sources
    return [
        level
        for level in sources
        if int(getattr(level, "timeframe_minutes", 0)) >= int(minimum_timeframe)
    ]


# v6 introduced this optional threshold after v5/v4 had already fixed the semantic
# source set.  A zero default preserves the v4 semantics instead of inventing a new
# filter, while the compatibility wrapper keeps future nonzero settings meaningful.
core.MINIMUM_SOURCE_TIMEFRAME = int(getattr(core, "MINIMUM_SOURCE_TIMEFRAME", 0))
core.direction_sources = _direction_sources_compat


if __name__ == "__main__":
    core.main()
