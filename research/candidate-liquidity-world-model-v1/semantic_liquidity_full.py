"""Shared semantic metadata contract for liquidity pools and dynamic boundaries.

This module intentionally contains only the immutable metadata record consumed by the
auction and episode-policy layers.  It restores the narrow dependency that was missing
from the research branch without introducing a trading rule, score, label, or future
observation.  All fields describe information available when a pool or boundary is
constructed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class PoolMeta:
    """Point-in-time semantic attributes attached to a liquidity source.

    ``member_timeframes`` remains a string because existing producers serialize either
    one timeframe (for example ``"15"``) or a delimiter-separated set.  Consumers must
    treat it as provenance, not as a predictive label.
    """

    pool_kind: str = "UNKNOWN"
    member_count: int = 0
    member_timeframes: str = ""
    accumulated: bool = False
    direction_source: bool = False
    route_obstacle: bool = False
    semantic_weight: float = 0.0

    def __post_init__(self) -> None:
        if self.member_count < 0:
            raise ValueError("member_count must be non-negative")
        if not math.isfinite(float(self.semantic_weight)):
            raise ValueError("semantic_weight must be finite")


__all__ = ["PoolMeta"]
