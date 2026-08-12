"""Create immutable structural events and resolve break attempts."""
from __future__ import annotations

from typing import Iterable

from domain import Candle, Side
from market_structure_types import (
    BoundaryRole,
    StructuralBoundary,
    StructureEvent,
    StructureKind,
    StructurePath,
)


class MarketStructureEmitMixin:
    def _make_event(
        self,
        *,
        path: StructurePath,
        side: Side,
        boundaries: Iterable[StructuralBoundary],
        bar: Candle,
        index: int,
        extreme: float,
        break_index: int | None = None,
    ) -> StructureEvent:
        unique = {boundary.boundary_id: boundary for boundary in boundaries}
        grouped = sorted(unique.values(), key=self._priority, reverse=True)
        primary = grouped[0]
        origin = self._latest_origin(side, index if break_index is None else break_index)
        if path is StructurePath.ACCEPTANCE and primary.channel_id is not None:
            channel = self.channels.get(primary.channel_id)
            if (
                channel is not None
                and channel.last_bounce_boundary_id == primary.boundary_id
                and not channel.midline_reached_after_bounce
            ):
                path = StructurePath.CHANNEL_FAILURE_ACCEPTANCE
                self._inc("channel_midline_failure_acceptance")
        target = self._select_target(primary, side, path, bar, index)
        if path in {StructurePath.BOUNCE, StructurePath.FAKEOUT, StructurePath.TRAP_REENTRY}:
            stop_reference = (
                extreme - self.tick_size if side is Side.LONG else extreme + self.tick_size
            )
        else:
            stop_reference = (
                origin.price - self.tick_size
                if origin is not None and side is Side.LONG
                else origin.price + self.tick_size
                if origin is not None
                else extreme - self.tick_size
                if side is Side.LONG
                else extreme + self.tick_size
            )
        self._sequence += 1
        event = StructureEvent(
            event_id=f"{self.symbol}:{self.timeframe_minutes}m:STRUCTURE_EVENT:{self._sequence:08d}",
            path=path,
            side=side,
            primary_boundary_id=primary.boundary_id,
            supporting_boundary_ids=tuple(item.boundary_id for item in grouped[1:]),
            interaction_index=index,
            interaction_time_ns=bar.ts_close_ns,
            interaction_extreme=extreme,
            reference_close=bar.close,
            stop_reference=stop_reference,
            target_boundary_id=None if target is None else target.boundary_id,
            target_price_at_interaction=None if target is None else target.level_at(bar.ts_close_ns),
            origin_pivot_id=None if origin is None else origin.pivot_id,
            origin_price=None if origin is None else origin.price,
            structure_kind=primary.kind,
            channel_id=primary.channel_id,
            rule_provenance=self.SOURCE_RULES + self.TRANSLATION_RULES,
        )
        if path in {StructurePath.BOUNCE, StructurePath.FAKEOUT, StructurePath.TRAP_REENTRY}:
            for boundary in grouped:
                boundary.rejection_used = True
                if boundary.first_touch_index is None:
                    boundary.first_touch_index = index
                    boundary.first_touch_time_ns = bar.ts_close_ns
                if boundary.kind in {StructureKind.SWING_HIGH, StructureKind.SWING_LOW}:
                    boundary.active = False
                    boundary.consumed_time_ns = bar.ts_close_ns
            self._mark_channel_interaction(primary, bar)
        else:
            for boundary in grouped:
                boundary.acceptance_used = True
                boundary.active = False
                boundary.consumed_time_ns = bar.ts_close_ns
            if primary.channel_id is not None:
                channel = self.channels.get(primary.channel_id)
                if channel is not None:
                    channel.active = False
                    for boundary_id in (
                        channel.lower_boundary_id,
                        channel.upper_boundary_id,
                        channel.midline_boundary_id,
                    ):
                        item = self.boundaries.get(boundary_id)
                        if item is not None:
                            item.active = False
        self._inc(f"structure_event_{path.value.lower()}")
        return event

    def _resolve_pending_breaks(self, bar: Candle, index: int) -> list[StructureEvent]:
        accepted: list[tuple[StructuralBoundary, _BreakAttempt, Side]] = []
        reentered: list[tuple[StructuralBoundary, _BreakAttempt, Side]] = []
        for boundary_id, pending in list(self._pending_breaks.items()):
            if index != pending.break_index + 1:
                if index > pending.break_index + 1:
                    self._pending_breaks.pop(boundary_id, None)
                    self._inc("break_attempt_expired_unresolved")
                continue
            boundary = self.boundaries.get(boundary_id)
            self._pending_breaks.pop(boundary_id, None)
            if boundary is None or not boundary.active or boundary.acceptance_used:
                continue
            level = boundary.level_at(bar.ts_close_ns)
            if boundary.role is BoundaryRole.SUPPORT:
                held = bar.open < level and bar.close < level
                inside = bar.close > level
                if held:
                    accepted.append((boundary, pending, Side.SHORT))
                elif inside:
                    reentered.append((boundary, pending, Side.LONG))
                else:
                    self._inc("break_attempt_unresolved_next_bar")
            else:
                held = bar.open > level and bar.close > level
                inside = bar.close < level
                if held:
                    accepted.append((boundary, pending, Side.LONG))
                elif inside:
                    reentered.append((boundary, pending, Side.SHORT))
                else:
                    self._inc("break_attempt_unresolved_next_bar")

        events: list[StructureEvent] = []
        for items, path in ((accepted, StructurePath.ACCEPTANCE), (reentered, StructurePath.TRAP_REENTRY)):
            by_side: dict[Side, list[tuple[StructuralBoundary, _BreakAttempt, Side]]] = {}
            for item in items:
                by_side.setdefault(item[2], []).append(item)
            for side, group in by_side.items():
                boundaries = [item[0] for item in group]
                if side is Side.LONG:
                    extreme = min(min(item[1].break_extreme for item in group), bar.low)
                else:
                    extreme = max(max(item[1].break_extreme for item in group), bar.high)
                events.append(
                    self._make_event(
                        path=path,
                        side=side,
                        boundaries=boundaries,
                        bar=bar,
                        index=index,
                        extreme=extreme,
                        break_index=min(item[1].break_index for item in group),
                    ),
                )
        return events


__all__ = ["MarketStructureEmitMixin"]
