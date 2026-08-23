"""Causal wick-pivot, trend-line and parallel-channel construction for v5.

The detector is deliberately geometric rather than profitable-by-construction:
all anchors are confirmed with closed bars, every line is derived from wick
pivots, and every channel is formed from two same-side pivots plus one opposite
pivot before a later interaction can be traded.
"""
from __future__ import annotations

from typing import Iterable

from domain import Candle, Side
from easychart_zones import ZoneSide

from contracts_v5 import (
    Channel,
    ObjectKind,
    Pivot,
    StructureFamily,
    StructureZone,
    TrendLine,
)


class CausalStructureBook:
    """One context-timeframe structure book.

    Pivot spans are not optimized here.  They are an explicit research
    translation retained from the previous candidate so local and larger
    auction legs can coexist without future information.
    """

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
            raise ValueError("pivot spans must be positive")
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.tick_size = tick_size
        self.pivot_spans = tuple(sorted(set(pivot_spans)))
        self.bars: list[Candle] = []
        self.pivots: list[Pivot] = []
        self.trend_lines: list[TrendLine] = []
        self.channels: list[Channel] = []
        self._pivot_ids: set[str] = set()
        self._active_pivots: dict[str, Pivot] = {}
        self._line_ids: set[str] = set()
        self._channel_ids: set[str] = set()
        self._superseded_lines: set[str] = set()
        self._superseded_channels: set[str] = set()
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    @staticmethod
    def _avg_range(items: Iterable[Candle], minimum: float) -> float:
        values = [bar.high - bar.low for bar in items]
        return max(sum(values) / max(len(values), 1), minimum)

    def _pivot_id(self, side: str, center: int, span: int) -> str:
        return f"{self.symbol}:{self.timeframe_minutes}m:PIVOT:{side}:{center}:s{span}"

    def _register_pivots(self, observed_index: int) -> list[Pivot]:
        created: list[Pivot] = []
        for span in self.pivot_spans:
            center = observed_index - span
            if center < span:
                continue
            window = self.bars[center - span : center + span + 1]
            if len(window) != 2 * span + 1:
                continue
            pivot_bar = self.bars[center]
            highs = [bar.high for bar in window]
            lows = [bar.low for bar in window]
            unique_high = pivot_bar.high == max(highs) and highs.count(pivot_bar.high) == 1
            unique_low = pivot_bar.low == min(lows) and lows.count(pivot_bar.low) == 1
            left = window[:span]
            right = window[span + 1 :]
            local_range = self._avg_range(left + right, self.tick_size)
            if unique_high:
                prominence = min(
                    pivot_bar.high - min(bar.low for bar in left),
                    pivot_bar.high - min(bar.low for bar in right),
                )
                item = self._create_pivot(
                    side="HIGH",
                    center=center,
                    span=span,
                    price=pivot_bar.high,
                    strength=prominence / local_range,
                    observed_index=observed_index,
                )
                if item is not None:
                    created.append(item)
            if unique_low:
                prominence = min(
                    max(bar.high for bar in left) - pivot_bar.low,
                    max(bar.high for bar in right) - pivot_bar.low,
                )
                item = self._create_pivot(
                    side="LOW",
                    center=center,
                    span=span,
                    price=pivot_bar.low,
                    strength=prominence / local_range,
                    observed_index=observed_index,
                )
                if item is not None:
                    created.append(item)
        return created

    def _create_pivot(
        self,
        *,
        side: str,
        center: int,
        span: int,
        price: float,
        strength: float,
        observed_index: int,
    ) -> Pivot | None:
        pivot_id = self._pivot_id(side, center, span)
        if pivot_id in self._pivot_ids:
            return None
        pivot_bar = self.bars[center]
        observed_bar = self.bars[observed_index]
        pivot = Pivot(
            pivot_id=pivot_id,
            side=side,
            price=price,
            index=center,
            event_time_ns=pivot_bar.ts_close_ns,
            observed_index=observed_index,
            observed_time_ns=observed_bar.ts_close_ns,
            span=span,
            strength_ratio=strength,
        )
        self._pivot_ids.add(pivot_id)
        self._active_pivots[pivot_id] = pivot
        self.pivots.append(pivot)
        self._inc(f"pivot_{side.lower()}_confirmed")
        return pivot

    def _line_value(self, first: Pivot, second: Pivot, time_ns: int) -> float:
        slope = (second.price - first.price) / (second.event_time_ns - first.event_time_ns)
        return first.price + slope * (time_ns - first.event_time_ns)

    def _line_respected(self, first: Pivot, second: Pivot, side: str) -> bool:
        for bar in self.bars[first.index : second.index + 1]:
            expected = self._line_value(first, second, bar.ts_close_ns)
            if side == "LOW" and bar.low < expected - self.tick_size:
                return False
            if side == "HIGH" and bar.high > expected + self.tick_size:
                return False
        return True

    def _compatible_previous(self, pivot: Pivot) -> Pivot | None:
        candidates = [
            item
            for item in self.pivots
            if item.pivot_id != pivot.pivot_id
            and item.side == pivot.side
            and item.span == pivot.span
            and item.index < pivot.index
            and (
                (pivot.side == "LOW" and item.price < pivot.price)
                or (pivot.side == "HIGH" and item.price > pivot.price)
            )
        ]
        for item in sorted(candidates, key=lambda value: value.index, reverse=True):
            if self._line_respected(item, pivot, pivot.side):
                return item
        return None

    def _build_trend_line(self, pivot: Pivot) -> TrendLine | None:
        first = self._compatible_previous(pivot)
        if first is None:
            return None
        kind = ObjectKind.UPTREND_LINE if pivot.side == "LOW" else ObjectKind.DOWNTREND_LINE
        side = ZoneSide.SUPPORT if pivot.side == "LOW" else ZoneSide.RESISTANCE
        line_id = f"{self.symbol}:{self.timeframe_minutes}m:{kind.value}:{first.pivot_id}|{pivot.pivot_id}"
        if line_id in self._line_ids:
            return None
        line = TrendLine(
            structure_id=line_id,
            kind=kind,
            side=side,
            timeframe_minutes=self.timeframe_minutes,
            first_pivot_id=first.pivot_id,
            second_pivot_id=pivot.pivot_id,
            first_time_ns=first.event_time_ns,
            second_time_ns=pivot.event_time_ns,
            first_price=first.price,
            second_price=pivot.price,
            observed_time_ns=max(first.observed_time_ns, pivot.observed_time_ns),
            pivot_span=pivot.span,
            strength_ratio=min(first.strength_ratio, pivot.strength_ratio),
        )
        for old in self.trend_lines:
            if old.kind is kind and old.pivot_span == pivot.span and old.second_pivot_id == first.pivot_id:
                self._superseded_lines.add(old.structure_id)
        self._line_ids.add(line_id)
        self.trend_lines.append(line)
        self._inc(f"{kind.value.lower()}_created")
        return line

    def _opposite_pivot_between(self, first: Pivot, second: Pivot) -> Pivot | None:
        side = "HIGH" if first.side == "LOW" else "LOW"
        candidates = [
            item
            for item in self.pivots
            if item.side == side
            and first.index < item.index < second.index
            and item.observed_time_ns <= second.observed_time_ns
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.span, item.strength_ratio, item.index))

    def _channel_respected(self, channel: Channel, start_index: int, end_index: int) -> bool:
        for bar in self.bars[start_index : end_index + 1]:
            lower = channel.lower_at(bar.ts_close_ns)
            upper = channel.upper_at(bar.ts_close_ns)
            if bar.low < lower - self.tick_size or bar.high > upper + self.tick_size:
                return False
        return True

    def _build_channel(self, line: TrendLine, second: Pivot) -> Channel | None:
        first = next(item for item in self.pivots if item.pivot_id == line.first_pivot_id)
        opposite = self._opposite_pivot_between(first, second)
        if opposite is None:
            return None
        line_at_opposite = line.value_at(opposite.event_time_ns)
        if line.kind is ObjectKind.UPTREND_LINE:
            direction = "ASCENDING"
            offset = opposite.price - line_at_opposite
        else:
            direction = "DESCENDING"
            offset = line_at_opposite - opposite.price
        if offset <= 2.0 * self.tick_size:
            self._inc("channel_rejected_nonpositive_width")
            return None
        channel_id = (
            f"{self.symbol}:{self.timeframe_minutes}m:CHANNEL:{direction}:"
            f"{first.pivot_id}|{opposite.pivot_id}|{second.pivot_id}"
        )
        if channel_id in self._channel_ids:
            return None
        channel = Channel(
            channel_id=channel_id,
            timeframe_minutes=self.timeframe_minutes,
            direction=direction,
            main_first_pivot_id=first.pivot_id,
            main_second_pivot_id=second.pivot_id,
            opposite_pivot_id=opposite.pivot_id,
            first_time_ns=first.event_time_ns,
            second_time_ns=second.event_time_ns,
            first_price=first.price,
            second_price=second.price,
            offset=offset,
            observed_time_ns=max(first.observed_time_ns, second.observed_time_ns, opposite.observed_time_ns),
            pivot_span=min(first.span, second.span),
            strength_ratio=min(first.strength_ratio, second.strength_ratio, opposite.strength_ratio),
        )
        if not self._channel_respected(channel, first.index, second.index):
            self._inc("channel_rejected_historical_violation")
            return None
        for old in self.channels:
            if (
                old.direction == direction
                and old.pivot_span == channel.pivot_span
                and old.main_second_pivot_id == first.pivot_id
            ):
                self._superseded_channels.add(old.channel_id)
        self._channel_ids.add(channel_id)
        self.channels.append(channel)
        self._inc(f"channel_{direction.lower()}_created")
        return channel

    def on_bar(self, bar: Candle) -> tuple[list[Pivot], list[TrendLine], list[Channel]]:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("context bars must arrive in increasing close time")
        self.bars.append(bar)
        observed_index = len(self.bars) - 1
        pivots = self._register_pivots(observed_index)
        lines: list[TrendLine] = []
        channels: list[Channel] = []
        for pivot in pivots:
            line = self._build_trend_line(pivot)
            if line is None:
                continue
            lines.append(line)
            channel = self._build_channel(line, pivot)
            if channel is not None:
                channels.append(channel)
        return pivots, lines, channels

    def observe_price(self, bar: Candle) -> None:
        """Update objective lifecycle after scenario classification for this bar."""
        for pivot_id, pivot in list(self._active_pivots.items()):
            if bar.ts_close_ns <= pivot.observed_time_ns:
                continue
            touched = bar.high >= pivot.price if pivot.side == "HIGH" else bar.low <= pivot.price
            if touched and pivot.first_touch_time_ns is None:
                pivot.first_touch_time_ns = bar.ts_close_ns
                pivot.first_touch_index = len(self.bars) - 1
                self._inc(f"pivot_{pivot.side.lower()}_first_touch")
            if touched and pivot.consumed_time_ns is None:
                pivot.consumed = True
                pivot.consumed_time_ns = bar.ts_close_ns
                self._active_pivots.pop(pivot_id, None)

    def active_trend_lines(self, time_ns: int) -> list[TrendLine]:
        return [
            line
            for line in self.trend_lines
            if line.structure_id not in self._superseded_lines and line.observed_time_ns < time_ns
        ]

    def active_channels(self, time_ns: int) -> list[Channel]:
        return [
            channel
            for channel in self.channels
            if channel.channel_id not in self._superseded_channels and channel.observed_time_ns < time_ns
        ]

    def _horizontal_snapshot(self, pivot: Pivot, time_ns: int) -> StructureZone:
        if pivot.side == "HIGH":
            side = ZoneSide.RESISTANCE
            kind = ObjectKind.HORIZONTAL_RESISTANCE
            lower, upper = pivot.price, pivot.price + self.tick_size
            invalidation = upper + self.tick_size
        else:
            side = ZoneSide.SUPPORT
            kind = ObjectKind.HORIZONTAL_SUPPORT
            lower, upper = pivot.price - self.tick_size, pivot.price
            invalidation = lower - self.tick_size
        return StructureZone(
            zone_id=f"{pivot.pivot_id}:SNAP:{time_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=side,
            timeframe_minutes=self.timeframe_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=pivot.price,
            formed_index=pivot.index,
            formed_time_ns=pivot.event_time_ns,
            observed_time_ns=pivot.observed_time_ns,
            formation_indices=(pivot.index,),
            strength_ratio=pivot.strength_ratio,
            source_structure_id=pivot.pivot_id,
            source_pivot_span=pivot.span,
            first_touch_index=pivot.first_touch_index,
            first_touch_time_ns=pivot.first_touch_time_ns,
            consumed=pivot.consumed and (pivot.consumed_time_ns or 0) < time_ns,
        )

    def _line_snapshot(self, line: TrendLine, time_ns: int) -> StructureZone:
        value = line.value_at(time_ns)
        return StructureZone(
            zone_id=f"{line.structure_id}:SNAP:{time_ns}",
            kind=line.kind,
            family=StructureFamily.TREND_LINE,
            side=line.side,
            timeframe_minutes=self.timeframe_minutes,
            lower=value - self.tick_size,
            upper=value + self.tick_size,
            invalidation=value - 2.0 * self.tick_size if line.side is ZoneSide.SUPPORT else value + 2.0 * self.tick_size,
            impulse_extreme=value,
            formed_index=0,
            formed_time_ns=line.second_time_ns,
            observed_time_ns=line.observed_time_ns,
            formation_indices=(),
            strength_ratio=line.strength_ratio,
            source_structure_id=line.structure_id,
            source_pivot_span=line.pivot_span,
        )

    def channel_edge_snapshot(self, channel: Channel, edge: str, time_ns: int) -> StructureZone:
        if edge not in {"LOWER", "UPPER"}:
            raise ValueError("channel edge must be LOWER or UPPER")
        if edge == "LOWER":
            value = channel.lower_at(time_ns)
            side = ZoneSide.SUPPORT
            kind = (
                ObjectKind.ASCENDING_CHANNEL_LOWER
                if channel.direction == "ASCENDING"
                else ObjectKind.DESCENDING_CHANNEL_LOWER
            )
            invalidation = value - 2.0 * self.tick_size
        else:
            value = channel.upper_at(time_ns)
            side = ZoneSide.RESISTANCE
            kind = (
                ObjectKind.ASCENDING_CHANNEL_UPPER
                if channel.direction == "ASCENDING"
                else ObjectKind.DESCENDING_CHANNEL_UPPER
            )
            invalidation = value + 2.0 * self.tick_size
        return StructureZone(
            zone_id=f"{channel.channel_id}:{edge}:SNAP:{time_ns}",
            kind=kind,
            family=StructureFamily.CHANNEL,
            side=side,
            timeframe_minutes=self.timeframe_minutes,
            lower=value - self.tick_size,
            upper=value + self.tick_size,
            invalidation=invalidation,
            impulse_extreme=value,
            formed_index=0,
            formed_time_ns=channel.second_time_ns,
            observed_time_ns=channel.observed_time_ns,
            formation_indices=(),
            strength_ratio=channel.strength_ratio,
            source_structure_id=f"{channel.channel_id}:{edge}",
            source_pivot_span=channel.pivot_span,
        )

    def boundaries_at(self, time_ns: int) -> list[StructureZone]:
        output: list[StructureZone] = []
        for pivot in self._active_pivots.values():
            if pivot.observed_time_ns < time_ns:
                output.append(self._horizontal_snapshot(pivot, time_ns))
        output.extend(self._line_snapshot(line, time_ns) for line in self.active_trend_lines(time_ns))
        for channel in self.active_channels(time_ns):
            output.append(self.channel_edge_snapshot(channel, "LOWER", time_ns))
            output.append(self.channel_edge_snapshot(channel, "UPPER", time_ns))
        return output

    def snapshot_for(self, zone: StructureZone, time_ns: int) -> StructureZone:
        """Project a previously observed structure to ``time_ns`` without new information.

        Horizontal levels are constant. Trend lines and channels are geometric
        objects, so a later reclaim/retest must use the line value at that later
        timestamp rather than the interaction-bar snapshot.
        """
        if zone.family is StructureFamily.HORIZONTAL:
            pivot = self.pivot_for_structure(zone.source_structure_id)
            return self._horizontal_snapshot(pivot, time_ns) if pivot is not None else zone
        if zone.family is StructureFamily.TREND_LINE:
            line = next(
                (item for item in self.trend_lines if item.structure_id == zone.source_structure_id),
                None,
            )
            return self._line_snapshot(line, time_ns) if line is not None else zone
        if zone.family is StructureFamily.CHANNEL:
            channel = self.channel_for_boundary(zone.source_structure_id)
            if channel is None:
                return zone
            edge = zone.source_structure_id.rsplit(":", 1)[-1]
            return self.channel_edge_snapshot(channel, edge, time_ns)
        return zone

    def channel_by_id(self, channel_id: str) -> Channel | None:
        return next((item for item in self.channels if item.channel_id == channel_id), None)

    def channel_for_boundary(self, source_structure_id: str) -> Channel | None:
        channel_id = source_structure_id.rsplit(":", 1)[0]
        return self.channel_by_id(channel_id)

    def pivot_for_structure(self, source_structure_id: str) -> Pivot | None:
        return next((item for item in self.pivots if item.pivot_id == source_structure_id), None)

    def target_for(
        self,
        side: Side,
        *,
        interaction_time_ns: int,
        source_span: int,
        current_high: float,
        current_low: float,
    ) -> tuple[StructureZone, float] | None:
        wanted = "HIGH" if side is Side.LONG else "LOW"
        candidates = [
            pivot
            for pivot in self.pivots
            if pivot.side == wanted
            and pivot.span >= source_span
            and pivot.observed_time_ns < interaction_time_ns
            and not (
                pivot.consumed_time_ns is not None
                and pivot.consumed_time_ns < interaction_time_ns
            )
            and (
                (side is Side.LONG and pivot.price > current_high)
                or (side is Side.SHORT and pivot.price < current_low)
            )
        ]
        if not candidates:
            return None
        pivot = (
            min(candidates, key=lambda item: (item.price, -item.span))
            if side is Side.LONG
            else max(candidates, key=lambda item: (item.price, item.span))
        )
        return self._horizontal_snapshot(pivot, interaction_time_ns), pivot.price

    def acceptance_origin(
        self,
        side: Side,
        *,
        before_time_ns: int,
        source_span: int,
    ) -> Pivot | None:
        wanted = "LOW" if side is Side.LONG else "HIGH"
        candidates = [
            pivot
            for pivot in self.pivots
            if pivot.side == wanted
            and pivot.span >= source_span
            and pivot.observed_time_ns < before_time_ns
        ]
        return max(candidates, key=lambda item: (item.event_time_ns, item.span), default=None)

    def target_spent_after(self, zone: StructureZone, interaction_time_ns: int) -> bool:
        pivot = self.pivot_for_structure(zone.source_structure_id)
        return bool(
            pivot is not None
            and pivot.consumed_time_ns is not None
            and pivot.consumed_time_ns > interaction_time_ns
        )
