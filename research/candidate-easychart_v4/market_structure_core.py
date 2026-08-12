"""Causal pivot, trendline and exactly-parallel channel construction."""
from __future__ import annotations

from domain import Candle, Side
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
    _BreakAttempt,
)

class MarketStructureState:
    """Closed-bar, wick-based trendline/channel/liquidity state."""

    SOURCE_RULES = (
        "SOURCE_EXPLICIT:TRENDLINES_AND_CHANNELS_USE_WICKS",
        "SOURCE_EXPLICIT:CHANNEL_LINES_ARE_EXACTLY_PARALLEL",
        "SOURCE_EXPLICIT:CHANNEL_NEEDS_THREE_POINTS_AND_FIRST_TRADE_IS_FOURTH_POINT",
        "SOURCE_EXPLICIT:FAKEOUT_CLOSES_BACK_INSIDE_STRUCTURE",
        "SOURCE_EXPLICIT:REAL_CHANNEL_BREAK_CLOSES_OUTSIDE_AND_NEXT_BAR_OPENS_OUTSIDE",
        "SOURCE_EXPLICIT:BREAKOUT_ENTRY_USES_RETEST_AND_BREAK_LEG_ORIGIN_INVALIDATION",
        "SOURCE_EXPLICIT:CHANNEL_REJECTION_TARGETS_OPPOSITE_BOUNDARY",
        "SOURCE_EXPLICIT:CHANNEL_MIDLINE_FAILURE_SIGNALS_WEAKENING",
    )
    TRANSLATION_RULES = (
        "HUMAN_NATURAL_INFERENCE:CONFIRMED_WICK_PIVOTS_REPLACE_MANUAL_MAGNET_ANCHORS",
        "HUMAN_NATURAL_INFERENCE:CONSECUTIVE_SAME_SIDE_PIVOTS_DEFINE_CURRENT_LINE",
        "HUMAN_NATURAL_INFERENCE:ONE_CONTEXT_BAR_INTERACTION_IS_ONE_CAUSAL_EPISODE",
        "RESEARCH_HYPOTHESIS:NO_FIXED_ANGLE_GATE",
        "RESEARCH_HYPOTHESIS:CHANNEL_WIDTH_EXTENSION_IS_FALLBACK_BREAKOUT_OBJECTIVE",
    )

    def __init__(
        self,
        symbol: str,
        timeframe_minutes: int,
        tick_size: float,
        *,
        pivot_spans: tuple[int, ...] = (2, 6),
    ) -> None:
        if timeframe_minutes <= 0 or tick_size <= 0.0:
            raise ValueError("timeframe and tick size must be positive")
        if not pivot_spans or any(span <= 0 for span in pivot_spans):
            raise ValueError("pivot spans must contain positive integers")
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.tick_size = tick_size
        self.pivot_spans = tuple(sorted(set(pivot_spans)))
        self.bars: list[Candle] = []
        self.pivots: list[ConfirmedPivot] = []
        self.boundaries: dict[str, StructuralBoundary] = {}
        self.zones: list[StructuralBoundary] = []
        self.channels: dict[str, ChannelState] = {}
        self._pivots_by_key: dict[tuple[int, PivotKind], list[ConfirmedPivot]] = {}
        self._current_line: dict[tuple[int, BoundaryRole], str] = {}
        self._current_channel: dict[tuple[int, ChannelDirection], str] = {}
        self._pending_breaks: dict[str, _BreakAttempt] = {}
        self._synthetic_targets: dict[str, StructuralBoundary] = {}
        self._sequence = 0
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def active_zones(self) -> list[StructuralBoundary]:
        return [zone for zone in self.zones if zone.active]

    def find_boundary(self, boundary_id: str) -> StructuralBoundary | None:
        return self.boundaries.get(boundary_id) or self._synthetic_targets.get(boundary_id)

    def _pivot_id(self, kind: PivotKind, center: int, span: int) -> str:
        return f"{self.symbol}:{self.timeframe_minutes}m:PIVOT:{kind.value}:{center}:s{span}"

    def _boundary_id(self, kind: StructureKind, *parts: object) -> str:
        joined = ":".join(str(part) for part in parts)
        return f"{self.symbol}:{self.timeframe_minutes}m:{kind.value}:{joined}"

    def _strength(self, center: int, span: int, kind: PivotKind) -> float:
        left = self.bars[center - span : center]
        right = self.bars[center + 1 : center + span + 1]
        pivot = self.bars[center]
        if kind is PivotKind.HIGH:
            prominence = min(
                pivot.high - min(item.low for item in left),
                pivot.high - min(item.low for item in right),
            )
        else:
            prominence = min(
                max(item.high for item in left) - pivot.low,
                max(item.high for item in right) - pivot.low,
            )
        avg_range = sum(item.high - item.low for item in left + right) / max(len(left) + len(right), 1)
        return prominence / max(avg_range, self.tick_size)

    def _register_boundary(self, boundary: StructuralBoundary) -> StructuralBoundary:
        if boundary.boundary_id in self.boundaries:
            return self.boundaries[boundary.boundary_id]
        self.boundaries[boundary.boundary_id] = boundary
        self.zones.append(boundary)
        self._inc(f"{boundary.kind.value.lower()}_created")
        return boundary

    def _horizontal_boundary(self, pivot: ConfirmedPivot) -> StructuralBoundary:
        kind = StructureKind.SWING_HIGH if pivot.kind is PivotKind.HIGH else StructureKind.SWING_LOW
        role = BoundaryRole.RESISTANCE if pivot.kind is PivotKind.HIGH else BoundaryRole.SUPPORT
        boundary = StructuralBoundary(
            boundary_id=self._boundary_id(kind, pivot.index, f"s{pivot.span}"),
            kind=kind,
            role=role,
            timeframe_minutes=self.timeframe_minutes,
            observed_time_ns=pivot.observed_time_ns,
            observed_index=pivot.observed_index,
            anchor_1_time_ns=pivot.event_time_ns,
            anchor_1_price=pivot.price,
            anchor_2_time_ns=pivot.event_time_ns,
            anchor_2_price=pivot.price,
            strength_ratio=pivot.strength_ratio,
            pivot_span=pivot.span,
        )
        return self._register_boundary(boundary)

    def _linear_boundary(
        self,
        first: ConfirmedPivot,
        second: ConfirmedPivot,
        kind: StructureKind,
        role: BoundaryRole,
    ) -> StructuralBoundary:
        boundary = StructuralBoundary(
            boundary_id=self._boundary_id(kind, first.index, second.index, f"s{first.span}"),
            kind=kind,
            role=role,
            timeframe_minutes=self.timeframe_minutes,
            observed_time_ns=max(first.observed_time_ns, second.observed_time_ns),
            observed_index=max(first.observed_index, second.observed_index),
            anchor_1_time_ns=first.event_time_ns,
            anchor_1_price=first.price,
            anchor_2_time_ns=second.event_time_ns,
            anchor_2_price=second.price,
            strength_ratio=min(first.strength_ratio, second.strength_ratio),
            pivot_span=first.span,
        )
        return self._register_boundary(boundary)

    def _parallel_boundary(
        self,
        *,
        source: StructuralBoundary,
        shift: float,
        kind: StructureKind,
        role: BoundaryRole,
        channel_id: str,
        observed_index: int,
        observed_time_ns: int,
        opposite_id: str | None = None,
        midline_id: str | None = None,
    ) -> StructuralBoundary:
        boundary = StructuralBoundary(
            boundary_id=self._boundary_id(kind, channel_id),
            kind=kind,
            role=role,
            timeframe_minutes=self.timeframe_minutes,
            observed_time_ns=observed_time_ns,
            observed_index=observed_index,
            anchor_1_time_ns=source.anchor_1_time_ns,
            anchor_1_price=source.anchor_1_price + shift,
            anchor_2_time_ns=source.anchor_2_time_ns,
            anchor_2_price=source.anchor_2_price + shift,
            strength_ratio=source.strength_ratio,
            pivot_span=source.pivot_span,
            channel_id=channel_id,
            opposite_boundary_id=opposite_id,
            midline_boundary_id=midline_id,
        )
        return self._register_boundary(boundary)

    def _retire_current_line(self, span: int, role: BoundaryRole) -> None:
        old_id = self._current_line.get((span, role))
        if old_id is not None and old_id in self.boundaries:
            self.boundaries[old_id].active = False
            self._inc("trendline_superseded")

    def _retire_current_channel(self, span: int, direction: ChannelDirection) -> None:
        old_id = self._current_channel.get((span, direction))
        if old_id is None:
            return
        channel = self.channels.get(old_id)
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
        self._inc("channel_superseded")

    def _build_channel(
        self,
        first: ConfirmedPivot,
        second: ConfirmedPivot,
        source: StructuralBoundary,
        direction: ChannelDirection,
    ) -> None:
        opposite_kind = PivotKind.HIGH if direction is ChannelDirection.ASCENDING else PivotKind.LOW
        opposite = [
            pivot
            for pivot in self._pivots_by_key.get((first.span, opposite_kind), [])
            if first.index < pivot.index < second.index
        ]
        if not opposite:
            self._inc("channel_missing_third_point")
            return
        if direction is ChannelDirection.ASCENDING:
            third = max(opposite, key=lambda pivot: (pivot.price - source.level_at(pivot.event_time_ns), pivot.index))
            width = third.price - source.level_at(third.event_time_ns)
        else:
            third = min(opposite, key=lambda pivot: (pivot.price - source.level_at(pivot.event_time_ns), pivot.index))
            width = source.level_at(third.event_time_ns) - third.price
        if width <= self.tick_size:
            self._inc("channel_nonpositive_width")
            return

        self._retire_current_channel(first.span, direction)
        channel_id = f"{self.symbol}:{self.timeframe_minutes}m:CHANNEL:{direction.value}:{first.index}:{second.index}:{third.index}:s{first.span}"
        observed_time = max(source.observed_time_ns, third.observed_time_ns)
        observed_index = max(source.observed_index, third.observed_index)
        if direction is ChannelDirection.ASCENDING:
            lower = self._parallel_boundary(
                source=source,
                shift=0.0,
                kind=StructureKind.CHANNEL_LOWER,
                role=BoundaryRole.SUPPORT,
                channel_id=channel_id,
                observed_index=observed_index,
                observed_time_ns=observed_time,
            )
            upper = self._parallel_boundary(
                source=source,
                shift=width,
                kind=StructureKind.CHANNEL_UPPER,
                role=BoundaryRole.RESISTANCE,
                channel_id=channel_id,
                observed_index=observed_index,
                observed_time_ns=observed_time,
            )
        else:
            upper = self._parallel_boundary(
                source=source,
                shift=0.0,
                kind=StructureKind.CHANNEL_UPPER,
                role=BoundaryRole.RESISTANCE,
                channel_id=channel_id,
                observed_index=observed_index,
                observed_time_ns=observed_time,
            )
            lower = self._parallel_boundary(
                source=source,
                shift=-width,
                kind=StructureKind.CHANNEL_LOWER,
                role=BoundaryRole.SUPPORT,
                channel_id=channel_id,
                observed_index=observed_index,
                observed_time_ns=observed_time,
            )
        mid = self._parallel_boundary(
            source=lower,
            shift=width / 2.0,
            kind=StructureKind.CHANNEL_MIDLINE,
            role=BoundaryRole.RESISTANCE,
            channel_id=channel_id,
            observed_index=observed_index,
            observed_time_ns=observed_time,
        )
        lower.opposite_boundary_id = upper.boundary_id
        upper.opposite_boundary_id = lower.boundary_id
        lower.midline_boundary_id = mid.boundary_id
        upper.midline_boundary_id = mid.boundary_id
        mid.opposite_boundary_id = None
        mid.midline_boundary_id = mid.boundary_id
        channel = ChannelState(
            channel_id=channel_id,
            direction=direction,
            timeframe_minutes=self.timeframe_minutes,
            pivot_span=first.span,
            observed_index=observed_index,
            observed_time_ns=observed_time,
            lower_boundary_id=lower.boundary_id,
            upper_boundary_id=upper.boundary_id,
            midline_boundary_id=mid.boundary_id,
            anchor_pivot_ids=(first.pivot_id, second.pivot_id, third.pivot_id),
        )
        self.channels[channel_id] = channel
        self._current_channel[(first.span, direction)] = channel_id
        self._inc("channel_confirmed_three_points")

    def _build_from_pivot(self, pivot: ConfirmedPivot) -> None:
        self._horizontal_boundary(pivot)
        key = (pivot.span, pivot.kind)
        history = self._pivots_by_key.setdefault(key, [])
        previous = history[-1] if history else None
        history.append(pivot)
        if previous is None:
            return
        if pivot.kind is PivotKind.LOW and pivot.price > previous.price:
            role = BoundaryRole.SUPPORT
            self._retire_current_line(pivot.span, role)
            line = self._linear_boundary(
                previous,
                pivot,
                StructureKind.TRENDLINE_SUPPORT,
                role,
            )
            self._current_line[(pivot.span, role)] = line.boundary_id
            self._build_channel(previous, pivot, line, ChannelDirection.ASCENDING)
        elif pivot.kind is PivotKind.HIGH and pivot.price < previous.price:
            role = BoundaryRole.RESISTANCE
            self._retire_current_line(pivot.span, role)
            line = self._linear_boundary(
                previous,
                pivot,
                StructureKind.TRENDLINE_RESISTANCE,
                role,
            )
            self._current_line[(pivot.span, role)] = line.boundary_id
            self._build_channel(previous, pivot, line, ChannelDirection.DESCENDING)

    def _confirm_pivots(self, observed_index: int) -> None:
        observed = self.bars[observed_index]
        for span in self.pivot_spans:
            center = observed_index - span
            if center < span:
                continue
            window = self.bars[center - span : center + span + 1]
            if len(window) != 2 * span + 1:
                continue
            pivot_bar = self.bars[center]
            highs = [item.high for item in window]
            lows = [item.low for item in window]
            candidates: list[PivotKind] = []
            if pivot_bar.high == max(highs) and highs.count(pivot_bar.high) == 1:
                candidates.append(PivotKind.HIGH)
            if pivot_bar.low == min(lows) and lows.count(pivot_bar.low) == 1:
                candidates.append(PivotKind.LOW)
            for kind in candidates:
                pivot_id = self._pivot_id(kind, center, span)
                if any(item.pivot_id == pivot_id for item in self.pivots):
                    continue
                pivot = ConfirmedPivot(
                    pivot_id=pivot_id,
                    kind=kind,
                    timeframe_minutes=self.timeframe_minutes,
                    span=span,
                    index=center,
                    event_time_ns=pivot_bar.ts_close_ns,
                    observed_index=observed_index,
                    observed_time_ns=observed.ts_close_ns,
                    price=pivot_bar.high if kind is PivotKind.HIGH else pivot_bar.low,
                    strength_ratio=self._strength(center, span, kind),
                )
                self.pivots.append(pivot)
                self._inc(f"pivot_{kind.value.lower()}_confirmed")
                self._build_from_pivot(pivot)


__all__ = ["MarketStructureState"]
