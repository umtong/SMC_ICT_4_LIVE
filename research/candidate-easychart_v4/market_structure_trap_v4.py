"""Source-faithful Fakeout versus Trap market-structure transitions.

EasyChart distinguishes two failed-break mechanisms:

* Fakeout returns rapidly after the first excursion and normally leaves one
  dominant wick/extreme.
* Trap first establishes a believable break, allows an inducement pause which
  forms the second high/low, and only then returns through the structure.

The baseline detector collapsed both mechanisms into ``TRAP_REENTRY`` whenever
the bar immediately after a break closed back inside.  This overlay preserves
the existing causal trendline/channel grammar but makes the distinction
explicit without adding an optimized elapsed-time threshold.  A confirmed
outside pivot supplies the source-described pause and second high/low.
"""
from __future__ import annotations

from dataclasses import dataclass

from domain import Candle, Side
from market_structure import MarketStructureDetector
from market_structure_types import (
    BoundaryRole,
    PivotKind,
    StructuralBoundary,
    StructureEvent,
    StructurePath,
    _BreakAttempt,
)


@dataclass(slots=True)
class _AcceptedTrapEpisode:
    event: StructureEvent
    boundary_id: str
    break_index: int
    break_time_ns: int
    excursion_extreme: float


class SourceFaithfulMarketStructureDetector(MarketStructureDetector):
    """Separate immediate Fakeout from delayed, induced Trap re-entry."""

    SOURCE_RULES = MarketStructureDetector.SOURCE_RULES + (
        "SOURCE_EXPLICIT:FAKEOUT_RETURNS_RAPIDLY_AFTER_THE_FALSE_BREAK",
        "SOURCE_EXPLICIT:TRAP_ALLOWS_AN_INDUCEMENT_PAUSE_AND_FORMS_A_DOUBLE_HIGH_OR_LOW",
    )
    TRANSLATION_RULES = MarketStructureDetector.TRANSLATION_RULES + (
        "HUMAN_NATURAL_INFERENCE:IMMEDIATE_NEXT_CONTEXT_REENTRY_IS_FAKEOUT_NOT_TRAP",
        "HUMAN_NATURAL_INFERENCE:BREAK_EXTREME_PLUS_LATER_CONFIRMED_OUTSIDE_PIVOT_REPRESENTS_TRAP_DOUBLE_TOP_OR_BOTTOM",
        "HUMAN_NATURAL_INFERENCE:FIRST_RETURN_INSIDE_CONSUMES_THE_ACCEPTED_BREAK_EPISODE",
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._accepted_trap_episodes: dict[str, _AcceptedTrapEpisode] = {}

    def _arm_accepted_trap_episode(
        self,
        event: StructureEvent,
        *,
        break_index: int,
        break_time_ns: int,
        excursion_extreme: float,
    ) -> None:
        self._accepted_trap_episodes[event.event_id] = _AcceptedTrapEpisode(
            event=event,
            boundary_id=event.primary_boundary_id,
            break_index=break_index,
            break_time_ns=break_time_ns,
            excursion_extreme=excursion_extreme,
        )
        self._inc("accepted_break_armed_for_source_trap")

    def _acceptance_target_reached(
        self,
        episode: _AcceptedTrapEpisode,
        bar: Candle,
    ) -> bool:
        target_id = episode.event.target_boundary_id
        if target_id is None:
            return False
        target = self.find_boundary(target_id)
        if target is None:
            return False
        level = target.level_at(bar.ts_close_ns)
        if episode.event.side is Side.LONG:
            return bar.high >= level
        return bar.low <= level

    def _outside_confirmation_pivot(
        self,
        episode: _AcceptedTrapEpisode,
        boundary: StructuralBoundary,
        *,
        reentry_index: int,
    ):
        wanted = PivotKind.HIGH if episode.event.side is Side.LONG else PivotKind.LOW
        half_tick = self.tick_size / 2.0
        candidates = []
        for pivot in self.pivots:
            # The break extreme is the first high/low.  A later confirmed
            # outside pivot is the source-described second high/low and pause.
            if pivot.kind is not wanted or pivot.index <= episode.break_index:
                continue
            # Never use a pivot whose right-hand confirmation closes on the
            # same bar as the re-entry decision.
            if pivot.observed_index >= reentry_index:
                continue
            level = boundary.level_at(pivot.event_time_ns)
            outside = (
                pivot.price > level + half_tick
                if episode.event.side is Side.LONG
                else pivot.price < level - half_tick
            )
            if outside:
                candidates.append(pivot)
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.index, item.span, item.pivot_id))

    def _resolve_pending_breaks(self, bar: Candle, index: int) -> list[StructureEvent]:
        accepted: list[tuple[StructuralBoundary, _BreakAttempt, Side]] = []
        immediate_fakeouts: list[tuple[StructuralBoundary, _BreakAttempt, Side]] = []
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
                    immediate_fakeouts.append((boundary, pending, Side.LONG))
                else:
                    self._inc("break_attempt_unresolved_next_bar")
            else:
                held = bar.open > level and bar.close > level
                inside = bar.close < level
                if held:
                    accepted.append((boundary, pending, Side.LONG))
                elif inside:
                    immediate_fakeouts.append((boundary, pending, Side.SHORT))
                else:
                    self._inc("break_attempt_unresolved_next_bar")

        events: list[StructureEvent] = []
        for items, path in (
            (accepted, StructurePath.ACCEPTANCE),
            (immediate_fakeouts, StructurePath.FAKEOUT),
        ):
            by_side: dict[Side, list[tuple[StructuralBoundary, _BreakAttempt, Side]]] = {}
            for item in items:
                by_side.setdefault(item[2], []).append(item)
            for side, group in by_side.items():
                boundaries = [item[0] for item in group]
                if side is Side.LONG:
                    event_extreme = min(
                        min(item[1].break_extreme for item in group),
                        bar.low,
                    )
                else:
                    event_extreme = max(
                        max(item[1].break_extreme for item in group),
                        bar.high,
                    )
                break_index = min(item[1].break_index for item in group)
                break_time_ns = min(item[1].break_time_ns for item in group)
                event = self._make_event(
                    path=path,
                    side=side,
                    boundaries=boundaries,
                    bar=bar,
                    index=index,
                    extreme=event_extreme,
                    break_index=break_index,
                )
                events.append(event)
                if path is StructurePath.FAKEOUT:
                    self._inc("immediate_break_reentry_reclassified_as_fakeout")
                    continue

                # Acceptance direction and excursion direction coincide.
                excursion_extreme = (
                    max(
                        max(item[1].break_extreme for item in group),
                        bar.high,
                    )
                    if side is Side.LONG
                    else min(
                        min(item[1].break_extreme for item in group),
                        bar.low,
                    )
                )
                self._arm_accepted_trap_episode(
                    event,
                    break_index=break_index,
                    break_time_ns=break_time_ns,
                    excursion_extreme=excursion_extreme,
                )
        return events

    def _resolve_accepted_traps(self, bar: Candle, index: int) -> list[StructureEvent]:
        confirmed: list[
            tuple[_AcceptedTrapEpisode, StructuralBoundary, Side]
        ] = []
        for event_id, episode in list(self._accepted_trap_episodes.items()):
            boundary = self.find_boundary(episode.boundary_id)
            if boundary is None:
                self._accepted_trap_episodes.pop(event_id, None)
                self._inc("accepted_break_trap_boundary_unavailable")
                continue

            # If the accepted move already delivered its predeclared
            # objective, a much later reversal is a new episode, not this Trap.
            if self._acceptance_target_reached(episode, bar):
                self._accepted_trap_episodes.pop(event_id, None)
                self._inc("accepted_break_target_reached_before_trap")
                continue

            if episode.event.side is Side.LONG:
                episode.excursion_extreme = max(episode.excursion_extreme, bar.high)
            else:
                episode.excursion_extreme = min(episode.excursion_extreme, bar.low)

            level = boundary.level_at(bar.ts_close_ns)
            half_tick = self.tick_size / 2.0
            reentered = (
                bar.close < level - half_tick
                if episode.event.side is Side.LONG
                else bar.close > level + half_tick
            )
            if not reentered:
                continue

            pivot = self._outside_confirmation_pivot(
                episode,
                boundary,
                reentry_index=index,
            )
            self._accepted_trap_episodes.pop(event_id, None)
            if pivot is None:
                self._inc("accepted_break_first_reentry_without_confirmed_outside_pivot")
                continue

            reverse_side = (
                Side.SHORT if episode.event.side is Side.LONG else Side.LONG
            )
            confirmed.append((episode, boundary, reverse_side))
            self._inc("accepted_break_source_trap_confirmed")

        events: list[StructureEvent] = []
        by_side: dict[
            Side,
            list[tuple[_AcceptedTrapEpisode, StructuralBoundary, Side]],
        ] = {}
        for item in confirmed:
            by_side.setdefault(item[2], []).append(item)
        for side, group in by_side.items():
            boundaries = [item[1] for item in group]
            extreme = (
                min(
                    min(item[0].excursion_extreme for item in group),
                    bar.low,
                )
                if side is Side.LONG
                else max(
                    max(item[0].excursion_extreme for item in group),
                    bar.high,
                )
            )
            events.append(
                self._make_event(
                    path=StructurePath.TRAP_REENTRY,
                    side=side,
                    boundaries=boundaries,
                    bar=bar,
                    index=index,
                    extreme=extreme,
                    break_index=min(item[0].break_index for item in group),
                ),
            )
        return events

    def on_bar(self, bar: Candle) -> list[StructureEvent]:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("bars must arrive in strictly increasing close time")
        self.bars.append(bar)
        index = len(self.bars) - 1
        self._update_channel_midlines(bar)
        events = self._resolve_accepted_traps(bar, index)
        events.extend(self._resolve_pending_breaks(bar, index))
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


__all__ = ["SourceFaithfulMarketStructureDetector"]
