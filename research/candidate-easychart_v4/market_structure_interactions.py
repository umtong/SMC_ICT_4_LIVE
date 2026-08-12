"""Current and lower-timeframe boundary interaction transitions."""
from __future__ import annotations

from domain import Candle, Side
from market_structure_types import (
    BoundaryRole,
    StructuralBoundary,
    StructureEvent,
    StructureKind,
    StructurePath,
    _BreakAttempt,
)


class MarketStructureInteractionMixin:
    def _current_interactions(self, bar: Candle, index: int) -> list[StructureEvent]:
        rejection: dict[tuple[Side, StructurePath], list[StructuralBoundary]] = {}
        extremes: dict[tuple[Side, StructurePath], float] = {}
        break_candidates: list[tuple[StructuralBoundary, float]] = []
        half_tick = self.tick_size / 2.0
        for boundary in list(self.boundaries.values()):
            if not self._eligible(boundary, bar, index):
                continue
            level = boundary.level_at(bar.ts_close_ns)
            if boundary.role is BoundaryRole.SUPPORT:
                if bar.close < level - half_tick and not boundary.acceptance_used:
                    break_candidates.append((boundary, bar.low))
                elif not boundary.rejection_used and bar.low < level - half_tick and bar.close > level:
                    key = (Side.LONG, StructurePath.FAKEOUT)
                    rejection.setdefault(key, []).append(boundary)
                    extremes[key] = min(extremes.get(key, bar.low), bar.low)
                elif not boundary.rejection_used and bar.low <= level + half_tick and bar.close >= level:
                    key = (Side.LONG, StructurePath.BOUNCE)
                    rejection.setdefault(key, []).append(boundary)
                    extremes[key] = min(extremes.get(key, bar.low), bar.low)
            else:
                if bar.close > level + half_tick and not boundary.acceptance_used:
                    break_candidates.append((boundary, bar.high))
                elif not boundary.rejection_used and bar.high > level + half_tick and bar.close < level:
                    key = (Side.SHORT, StructurePath.FAKEOUT)
                    rejection.setdefault(key, []).append(boundary)
                    extremes[key] = max(extremes.get(key, bar.high), bar.high)
                elif not boundary.rejection_used and bar.high >= level - half_tick and bar.close <= level:
                    key = (Side.SHORT, StructurePath.BOUNCE)
                    rejection.setdefault(key, []).append(boundary)
                    extremes[key] = max(extremes.get(key, bar.high), bar.high)

        for boundary, extreme in break_candidates:
            self._pending_breaks[boundary.boundary_id] = _BreakAttempt(
                boundary_id=boundary.boundary_id,
                break_index=index,
                break_time_ns=bar.ts_close_ns,
                break_extreme=extreme,
                break_close=bar.close,
            )
            self._inc("structural_break_attempt")

        events: list[StructureEvent] = []
        broken_roles = {boundary.role for boundary, _ in break_candidates}
        if BoundaryRole.SUPPORT in broken_roles:
            rejection.pop((Side.LONG, StructurePath.FAKEOUT), None)
            rejection.pop((Side.LONG, StructurePath.BOUNCE), None)
            self._inc("nested_support_hold_suppressed_by_higher_break")
        if BoundaryRole.RESISTANCE in broken_roles:
            rejection.pop((Side.SHORT, StructurePath.FAKEOUT), None)
            rejection.pop((Side.SHORT, StructurePath.BOUNCE), None)
            self._inc("nested_resistance_hold_suppressed_by_higher_break")
        # A wick sweep is semantically stronger than an ordinary touch on the
        # same side and bar. Merge all touched structures into that one event.
        for side in (Side.LONG, Side.SHORT):
            fake = rejection.get((side, StructurePath.FAKEOUT), [])
            bounce = rejection.get((side, StructurePath.BOUNCE), [])
            if fake:
                combined = fake + [item for item in bounce if item not in fake]
                events.append(
                    self._make_event(
                        path=StructurePath.FAKEOUT,
                        side=side,
                        boundaries=combined,
                        bar=bar,
                        index=index,
                        extreme=extremes[(side, StructurePath.FAKEOUT)],
                    ),
                )
            elif bounce:
                events.append(
                    self._make_event(
                        path=StructurePath.BOUNCE,
                        side=side,
                        boundaries=bounce,
                        bar=bar,
                        index=index,
                        extreme=extremes[(side, StructurePath.BOUNCE)],
                    ),
                )
        return events

    def observe_lower_bar(self, bar: Candle) -> list[StructureEvent]:
        """Observe a causally later lower-timeframe bar against known structure.

        Lower bars may confirm a bounce or wick-reclaim without waiting for the
        context candle to close. They never confirm structural acceptance; the
        source requires the channel/trendline timeframe close and next-bar hold
        for that transition.
        """
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            return []
        self._update_channel_midlines(bar)
        index = len(self.bars)
        rejection: dict[tuple[Side, StructurePath], list[StructuralBoundary]] = {}
        extremes: dict[tuple[Side, StructurePath], float] = {}
        broken_roles: set[BoundaryRole] = set()
        half_tick = self.tick_size / 2.0
        for boundary in list(self.boundaries.values()):
            if (
                not boundary.active
                or boundary.kind in {StructureKind.CHANNEL_MIDLINE, StructureKind.CHANNEL_EXTENSION}
                or bar.ts_close_ns <= boundary.observed_time_ns
            ):
                continue
            level = boundary.level_at(bar.ts_close_ns)
            if boundary.role is BoundaryRole.SUPPORT:
                if bar.close < level - half_tick:
                    broken_roles.add(BoundaryRole.SUPPORT)
                elif not boundary.rejection_used and bar.low < level - half_tick and bar.close > level:
                    key = (Side.LONG, StructurePath.FAKEOUT)
                    rejection.setdefault(key, []).append(boundary)
                    extremes[key] = min(extremes.get(key, bar.low), bar.low)
                elif not boundary.rejection_used and bar.low <= level + half_tick and bar.close >= level:
                    key = (Side.LONG, StructurePath.BOUNCE)
                    rejection.setdefault(key, []).append(boundary)
                    extremes[key] = min(extremes.get(key, bar.low), bar.low)
            else:
                if bar.close > level + half_tick:
                    broken_roles.add(BoundaryRole.RESISTANCE)
                elif not boundary.rejection_used and bar.high > level + half_tick and bar.close < level:
                    key = (Side.SHORT, StructurePath.FAKEOUT)
                    rejection.setdefault(key, []).append(boundary)
                    extremes[key] = max(extremes.get(key, bar.high), bar.high)
                elif not boundary.rejection_used and bar.high >= level - half_tick and bar.close <= level:
                    key = (Side.SHORT, StructurePath.BOUNCE)
                    rejection.setdefault(key, []).append(boundary)
                    extremes[key] = max(extremes.get(key, bar.high), bar.high)
        if BoundaryRole.SUPPORT in broken_roles:
            rejection.pop((Side.LONG, StructurePath.FAKEOUT), None)
            rejection.pop((Side.LONG, StructurePath.BOUNCE), None)
        if BoundaryRole.RESISTANCE in broken_roles:
            rejection.pop((Side.SHORT, StructurePath.FAKEOUT), None)
            rejection.pop((Side.SHORT, StructurePath.BOUNCE), None)
        events: list[StructureEvent] = []
        for side in (Side.LONG, Side.SHORT):
            fake = rejection.get((side, StructurePath.FAKEOUT), [])
            bounce = rejection.get((side, StructurePath.BOUNCE), [])
            if fake:
                combined = fake + [item for item in bounce if item not in fake]
                events.append(
                    self._make_event(
                        path=StructurePath.FAKEOUT,
                        side=side,
                        boundaries=combined,
                        bar=bar,
                        index=index,
                        extreme=extremes[(side, StructurePath.FAKEOUT)],
                    ),
                )
            elif bounce:
                events.append(
                    self._make_event(
                        path=StructurePath.BOUNCE,
                        side=side,
                        boundaries=bounce,
                        bar=bar,
                        index=index,
                        extreme=extremes[(side, StructurePath.BOUNCE)],
                    ),
                )
        return events

    def on_bar(self, bar: Candle) -> list[StructureEvent]:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("bars must arrive in strictly increasing close time")
        self.bars.append(bar)
        index = len(self.bars) - 1
        self._update_channel_midlines(bar)
        events = self._resolve_pending_breaks(bar, index)
        events.extend(self._current_interactions(bar, index))
        self._confirm_pivots(index)
        return sorted(
            events,
            key=lambda event: (
                event.interaction_time_ns,
                -self._priority(self.boundaries[event.primary_boundary_id])[0],
                event.event_id,
            ),
        )


__all__ = ["MarketStructureInteractionMixin"]
