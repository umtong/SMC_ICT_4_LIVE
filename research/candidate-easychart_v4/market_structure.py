"""Causal EasyChart market-structure grammar.

The implementation is split into data contracts, causal construction and
interaction state so each ambiguity can be tested without changing the public
API. All observations are closed-bar and wick based.
"""
from __future__ import annotations

from market_structure_core import MarketStructureState
from market_structure_emit import MarketStructureEmitMixin
from market_structure_interactions import MarketStructureInteractionMixin
from market_structure_targets import MarketStructureTargetMixin
from market_structure_types import (
    BoundaryRole,
    ChannelDirection,
    ChannelState,
    ConfirmedPivot,
    PivotKind,
    StructuralBoundary,
    StructureEvent,
    StructureKind,
    StructurePath,
)


class MarketStructureDetector(
    MarketStructureInteractionMixin,
    MarketStructureEmitMixin,
    MarketStructureTargetMixin,
    MarketStructureState,
):
    """Closed-bar, wick-defined trendline/channel/liquidity state."""


__all__ = [
    "BoundaryRole",
    "ChannelDirection",
    "ChannelState",
    "ConfirmedPivot",
    "MarketStructureDetector",
    "PivotKind",
    "StructuralBoundary",
    "StructureEvent",
    "StructureKind",
    "StructurePath",
]
