"""Causal horizontal box-range construction for EasyChart v4.

The source material treats a horizontal channel/box as a first-class market
structure and says that a channel needs at least three points before its first
trade at the fourth point.  A human chartist also treats support and resistance
as areas rather than requiring two wick prices to be bit-identical.

This overlay translates that ambiguity without an optimized price-distance
threshold:

* a pivot's rejection area is the observed wick tail, from body edge to wick;
* two same-side confirmed pivots represent one horizontal boundary only when
  those two rejection areas have a non-empty price intersection;
* an opposite confirmed pivot between them supplies the other boundary;
* the range becomes observable only after all three pivots are causally
  confirmed, and its first later interaction is the fourth point.

The tradable line uses the far edge of the common rejection area.  Thus a
Fakeout or accepted break must traverse the level shared by both prior
rejections; a merely nearby touch is not promoted by an ATR or percentage
buffer.
"""
from __future__ import annotations

from enum import Enum

from market_structure_trap_v4 import SourceFaithfulMarketStructureDetector
from market_structure_types import (
    BoundaryRole,
    ChannelState,
    ConfirmedPivot,
    PivotKind,
    StructuralBoundary,
    StructureKind,
)


class HorizontalRangeDirection(str, Enum):
    HORIZONTAL = "HORIZONTAL"


class HorizontalRangeMarketStructureDetector(
    SourceFaithfulMarketStructureDetector,
):
    """Add three-point horizontal box ranges to the existing structure grammar."""

    SOURCE_RULES = SourceFaithfulMarketStructureDetector.SOURCE_RULES + (
        "SOURCE_EXPLICIT:HORIZONTAL_CHANNEL_OR_BOX_RANGE_IS_A_CHANNEL_TYPE",
        "SOURCE_EXPLICIT:CHANNEL_NEEDS_THREE_POINTS_AND_FIRST_TRADE_IS_FOURTH_POINT",
        "SOURCE_EXPLICIT:CHANNEL_EDGES_ARE_LIQUIDITY_AND_OPPOSITE_EDGE_IS_OBJECTIVE",
    )
    TRANSLATION_RULES = SourceFaithfulMarketStructureDetector.TRANSLATION_RULES + (
        "HUMAN_NATURAL_INFERENCE:OVERLAPPING_CONFIRMED_WICK_REJECTION_AREAS_DEFINE_ONE_HORIZONTAL_LEVEL",
        "HUMAN_NATURAL_INFERENCE:FAR_EDGE_OF_COMMON_REJECTION_AREA_IS_THE_MACHINE_BREAK_LEVEL",
        "HUMAN_NATURAL_INFERENCE:ONE_OPPOSITE_PIVOT_BETWEEN_TWO_SAME_LEVEL_PIVOTS_COMPLETES_THE_THREE_POINT_BOX",
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._current_horizontal_range: dict[int, str] = {}
        self.horizontal_range_ids: set[str] = set()

    def is_horizontal_range(self, channel_id: str | None) -> bool:
        return channel_id is not None and channel_id in self.horizontal_range_ids

    def _pivot_rejection_band(self, pivot: ConfirmedPivot) -> tuple[float, float]:
        bar = self.bars[pivot.index]
        if pivot.kind is PivotKind.LOW:
            return bar.low, min(bar.open, bar.close)
        return max(bar.open, bar.close), bar.high

    def _common_rejection_band(
        self,
        first: ConfirmedPivot,
        second: ConfirmedPivot,
    ) -> tuple[float, float] | None:
        first_lower, first_upper = self._pivot_rejection_band(first)
        second_lower, second_upper = self._pivot_rejection_band(second)
        lower = max(first_lower, second_lower)
        upper = min(first_upper, second_upper)
        if not lower < upper:
            return None
        return lower, upper

    def _opposite_between(
        self,
        first: ConfirmedPivot,
        second: ConfirmedPivot,
    ) -> ConfirmedPivot | None:
        wanted = PivotKind.HIGH if first.kind is PivotKind.LOW else PivotKind.LOW
        candidates = [
            pivot
            for pivot in self._pivots_by_key.get((first.span, wanted), [])
            if first.index < pivot.index < second.index
        ]
        if not candidates:
            return None
        if wanted is PivotKind.HIGH:
            return max(candidates, key=lambda item: (item.price, item.index))
        return min(candidates, key=lambda item: (item.price, -item.index))

    def _retire_horizontal_range(self, span: int) -> None:
        channel_id = self._current_horizontal_range.get(span)
        if channel_id is None:
            return
        channel = self.channels.get(channel_id)
        if channel is None:
            return
        channel.active = False
        for boundary_id in (
            channel.lower_boundary_id,
            channel.upper_boundary_id,
            channel.midline_boundary_id,
        ):
            boundary = self.boundaries.get(boundary_id)
            if boundary is not None:
                boundary.active = False
        self._inc("horizontal_range_superseded")

    def _make_horizontal_range_boundary(
        self,
        *,
        boundary_id: str,
        kind: StructureKind,
        role: BoundaryRole,
        price: float,
        first: ConfirmedPivot,
        second: ConfirmedPivot,
        observed_index: int,
        observed_time_ns: int,
        strength_ratio: float,
        channel_id: str,
    ) -> StructuralBoundary:
        boundary = StructuralBoundary(
            boundary_id=boundary_id,
            kind=kind,
            role=role,
            timeframe_minutes=self.timeframe_minutes,
            observed_time_ns=observed_time_ns,
            observed_index=observed_index,
            anchor_1_time_ns=first.event_time_ns,
            anchor_1_price=price,
            anchor_2_time_ns=second.event_time_ns,
            anchor_2_price=price,
            strength_ratio=strength_ratio,
            pivot_span=first.span,
            channel_id=channel_id,
        )
        return self._register_boundary(boundary)

    def _build_horizontal_range(
        self,
        first: ConfirmedPivot,
        second: ConfirmedPivot,
        third: ConfirmedPivot,
        common_band: tuple[float, float],
    ) -> None:
        if first.kind is PivotKind.LOW:
            support_price = common_band[0]
            resistance_price = third.price
        else:
            support_price = third.price
            resistance_price = common_band[1]
        if resistance_price - support_price <= self.tick_size:
            self._inc("horizontal_range_nonpositive_width")
            return

        observed_index = max(
            first.observed_index,
            second.observed_index,
            third.observed_index,
        )
        observed_time_ns = max(
            first.observed_time_ns,
            second.observed_time_ns,
            third.observed_time_ns,
        )
        chronological = tuple(
            item.pivot_id
            for item in sorted((first, third, second), key=lambda item: item.index)
        )
        channel_id = (
            f"{self.symbol}:{self.timeframe_minutes}m:HORIZONTAL_RANGE:"
            f"{first.kind.value}:{first.index}:{third.index}:{second.index}:s{first.span}"
        )
        self._retire_horizontal_range(first.span)
        strength = min(
            first.strength_ratio,
            second.strength_ratio,
            third.strength_ratio,
        )
        lower = self._make_horizontal_range_boundary(
            boundary_id=self._boundary_id(StructureKind.CHANNEL_LOWER, channel_id),
            kind=StructureKind.CHANNEL_LOWER,
            role=BoundaryRole.SUPPORT,
            price=support_price,
            first=first,
            second=second,
            observed_index=observed_index,
            observed_time_ns=observed_time_ns,
            strength_ratio=strength,
            channel_id=channel_id,
        )
        upper = self._make_horizontal_range_boundary(
            boundary_id=self._boundary_id(StructureKind.CHANNEL_UPPER, channel_id),
            kind=StructureKind.CHANNEL_UPPER,
            role=BoundaryRole.RESISTANCE,
            price=resistance_price,
            first=first,
            second=second,
            observed_index=observed_index,
            observed_time_ns=observed_time_ns,
            strength_ratio=strength,
            channel_id=channel_id,
        )
        mid = self._make_horizontal_range_boundary(
            boundary_id=self._boundary_id(StructureKind.CHANNEL_MIDLINE, channel_id),
            kind=StructureKind.CHANNEL_MIDLINE,
            role=BoundaryRole.RESISTANCE,
            price=(support_price + resistance_price) / 2.0,
            first=first,
            second=second,
            observed_index=observed_index,
            observed_time_ns=observed_time_ns,
            strength_ratio=strength,
            channel_id=channel_id,
        )
        lower.opposite_boundary_id = upper.boundary_id
        upper.opposite_boundary_id = lower.boundary_id
        lower.midline_boundary_id = mid.boundary_id
        upper.midline_boundary_id = mid.boundary_id
        mid.midline_boundary_id = mid.boundary_id
        channel = ChannelState(
            channel_id=channel_id,
            direction=HorizontalRangeDirection.HORIZONTAL,  # type: ignore[arg-type]
            timeframe_minutes=self.timeframe_minutes,
            pivot_span=first.span,
            observed_index=observed_index,
            observed_time_ns=observed_time_ns,
            lower_boundary_id=lower.boundary_id,
            upper_boundary_id=upper.boundary_id,
            midline_boundary_id=mid.boundary_id,
            anchor_pivot_ids=chronological,  # type: ignore[arg-type]
        )
        self.channels[channel_id] = channel
        self._current_horizontal_range[first.span] = channel_id
        self.horizontal_range_ids.add(channel_id)
        self._inc("horizontal_range_confirmed_three_points")
        self._inc(f"horizontal_range_seed_{first.kind.value.lower()}_pair")

    def _build_from_pivot(self, pivot: ConfirmedPivot) -> None:
        super()._build_from_pivot(pivot)
        history = self._pivots_by_key.get((pivot.span, pivot.kind), [])
        if len(history) < 2:
            return
        first, second = history[-2:]
        common_band = self._common_rejection_band(first, second)
        if common_band is None:
            self._inc("horizontal_range_same_side_rejection_bands_disjoint")
            return
        third = self._opposite_between(first, second)
        if third is None:
            self._inc("horizontal_range_missing_opposite_third_point")
            return
        self._build_horizontal_range(first, second, third, common_band)


__all__ = [
    "HorizontalRangeDirection",
    "HorizontalRangeMarketStructureDetector",
]
