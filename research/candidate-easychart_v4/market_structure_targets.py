"""Structural objectives and channel interaction state."""
from __future__ import annotations

from domain import Candle, Side
from market_structure_types import (
    BoundaryRole,
    ChannelDirection,
    ConfirmedPivot,
    PivotKind,
    StructuralBoundary,
    StructureKind,
    StructurePath,
)


class MarketStructureTargetMixin:
    @staticmethod
    def _priority(boundary: StructuralBoundary) -> tuple[int, int, float, str]:
        semantic = {
            StructureKind.CHANNEL_LOWER: 3,
            StructureKind.CHANNEL_UPPER: 3,
            StructureKind.TRENDLINE_SUPPORT: 2,
            StructureKind.TRENDLINE_RESISTANCE: 2,
            StructureKind.SWING_LOW: 1,
            StructureKind.SWING_HIGH: 1,
            StructureKind.CHANNEL_MIDLINE: 0,
            StructureKind.CHANNEL_EXTENSION: 0,
        }[boundary.kind]
        return semantic, boundary.pivot_span, boundary.strength_ratio, boundary.boundary_id

    def _eligible(self, boundary: StructuralBoundary, bar: Candle, index: int) -> bool:
        if not boundary.active or boundary.kind in {StructureKind.CHANNEL_MIDLINE, StructureKind.CHANNEL_EXTENSION}:
            return False
        if index <= boundary.observed_index or bar.ts_close_ns <= boundary.observed_time_ns:
            return False
        return True

    def _latest_origin(self, side: Side, before_index: int) -> ConfirmedPivot | None:
        wanted = PivotKind.LOW if side is Side.LONG else PivotKind.HIGH
        candidates = [
            pivot
            for pivot in self.pivots
            if pivot.kind is wanted and pivot.index < before_index and pivot.observed_index < before_index
        ]
        return max(candidates, key=lambda pivot: (pivot.index, pivot.span)) if candidates else None

    def _nearest_pivot_target(
        self,
        side: Side,
        bar: Candle,
        event_time_ns: int,
    ) -> StructuralBoundary | None:
        wanted = BoundaryRole.RESISTANCE if side is Side.LONG else BoundaryRole.SUPPORT
        candidates: list[tuple[float, StructuralBoundary]] = []
        for boundary in self.boundaries.values():
            if boundary.kind not in {StructureKind.SWING_HIGH, StructureKind.SWING_LOW}:
                continue
            if not boundary.active or boundary.role is not wanted:
                continue
            if boundary.observed_time_ns >= event_time_ns:
                continue
            level = boundary.level_at(event_time_ns)
            if side is Side.LONG and level > bar.high:
                candidates.append((level, boundary))
            elif side is Side.SHORT and level < bar.low:
                candidates.append((level, boundary))
        if not candidates:
            return None
        if side is Side.LONG:
            return min(candidates, key=lambda item: (item[0], -item[1].pivot_span))[1]
        return max(candidates, key=lambda item: (item[0], item[1].pivot_span))[1]

    def _channel_extension_target(
        self,
        primary: StructuralBoundary,
        side: Side,
        ts_ns: int,
        observed_index: int,
    ) -> StructuralBoundary | None:
        if primary.channel_id is None:
            return None
        channel = self.channels.get(primary.channel_id)
        if channel is None:
            return None
        lower = self.boundaries[channel.lower_boundary_id]
        upper = self.boundaries[channel.upper_boundary_id]
        lower_level = lower.level_at(ts_ns)
        upper_level = upper.level_at(ts_ns)
        width = upper_level - lower_level
        if width <= self.tick_size:
            return None
        base = upper if side is Side.LONG else lower
        shift = width if side is Side.LONG else -width
        target_id = self._boundary_id(
            StructureKind.CHANNEL_EXTENSION,
            channel.channel_id,
            side.name,
            ts_ns,
        )
        target = StructuralBoundary(
            boundary_id=target_id,
            kind=StructureKind.CHANNEL_EXTENSION,
            role=BoundaryRole.RESISTANCE if side is Side.LONG else BoundaryRole.SUPPORT,
            timeframe_minutes=self.timeframe_minutes,
            observed_time_ns=ts_ns,
            observed_index=observed_index,
            anchor_1_time_ns=base.anchor_1_time_ns,
            anchor_1_price=base.anchor_1_price + shift,
            anchor_2_time_ns=base.anchor_2_time_ns,
            anchor_2_price=base.anchor_2_price + shift,
            strength_ratio=base.strength_ratio,
            pivot_span=base.pivot_span,
            channel_id=channel.channel_id,
            active=False,
        )
        self._synthetic_targets[target_id] = target
        return target

    def _select_target(
        self,
        primary: StructuralBoundary,
        side: Side,
        path: StructurePath,
        bar: Candle,
        index: int,
    ) -> StructuralBoundary | None:
        if path in {StructurePath.BOUNCE, StructurePath.FAKEOUT, StructurePath.TRAP_REENTRY}:
            if primary.channel_id is not None and primary.opposite_boundary_id is not None:
                return self.boundaries.get(primary.opposite_boundary_id)
            return self._nearest_pivot_target(side, bar, bar.ts_close_ns)
        target = self._nearest_pivot_target(side, bar, bar.ts_close_ns)
        if target is not None:
            return target
        return self._channel_extension_target(primary, side, bar.ts_close_ns, index)

    def _mark_channel_interaction(self, boundary: StructuralBoundary, bar: Candle) -> None:
        if boundary.channel_id is None:
            return
        channel = self.channels.get(boundary.channel_id)
        if channel is None:
            return
        if channel.first_interaction_time_ns is None:
            channel.first_interaction_time_ns = bar.ts_close_ns
            self._inc("channel_fourth_point_interaction")
        channel.last_bounce_boundary_id = boundary.boundary_id
        channel.last_bounce_time_ns = bar.ts_close_ns
        channel.midline_reached_after_bounce = False

    def _update_channel_midlines(self, bar: Candle) -> None:
        for channel in self.channels.values():
            if not channel.active or channel.last_bounce_boundary_id is None:
                continue
            if channel.last_bounce_time_ns is None or bar.ts_close_ns <= channel.last_bounce_time_ns:
                continue
            mid = self.boundaries[channel.midline_boundary_id].level_at(bar.ts_close_ns)
            bounced_from = self.boundaries[channel.last_bounce_boundary_id]
            if bounced_from.role is BoundaryRole.SUPPORT and bar.high >= mid:
                channel.midline_reached_after_bounce = True
            elif bounced_from.role is BoundaryRole.RESISTANCE and bar.low <= mid:
                channel.midline_reached_after_bounce = True


__all__ = ["MarketStructureTargetMixin"]
