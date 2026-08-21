#!/usr/bin/env python3
"""Candidate 1a overlay for the inherited auction-episode harvester.

This module deliberately reuses the v3-v7/episode implementation.  It repairs the
small interface drift created while the v4 semantic ledger, v5 first-return engine and
v6 sequential episode state were developed on separate branches, then executes the
complete inherited causal episode pipeline.
"""
from __future__ import annotations

from typing import Any

import auction_episode_harvest as episode

core = episode.core
narrative = core.core
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


# v6 calls helpers through the v5 module, while the helpers still live in the coherent
# narrative module nested at v5.core.  Expose the original implementations rather than
# copying or replacing their logic.
core.MINIMUM_SOURCE_TIMEFRAME = int(
    getattr(core, "MINIMUM_SOURCE_TIMEFRAME", narrative.MINIMUM_SOURCE_TIMEFRAME)
)
core.MAX_RESPONSE_BARS = int(
    getattr(core, "MAX_RESPONSE_BARS", narrative.MAX_RESPONSE_BARS)
)
core._atr_price = getattr(core, "_atr_price", narrative._atr_price)
core.direction_sources = _direction_sources_compat


if __name__ == "__main__":
    core.main()
