"""Source-faithful admission of market structures for the integrated candidate.

All confirmed pivots remain available as liquidity objectives.  Trade context
is stricter: visible structural swings, still-valid wick trend lines, and only
the channel edge whose fourth interaction is still in the future.  This is the
human chart-reading distinction between "a pivot exists" and "this is a
meaningful place to trade".
"""
from __future__ import annotations

import contracts_v5 as _contracts
from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import Channel, ObjectKind, Pivot, StructureFamily, StructureZone, TrendLine


MEANINGFUL_HORIZONTAL_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "TRADE_CONTEXT_USES_STRUCTURAL_OR_DIAGONAL_ANCHOR_PIVOTS_NOT_EVERY_LOCAL_PIVOT"
)
CHANNEL_FOURTH_POINT_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_THREE_ANCHOR_CHANNEL_ADMITS_ONLY_ITS_EXPECTED_FOURTH_EDGE_AS_FRESH_ROTATION_CONTEXT"
)
OBSERVABLE_STRUCTURE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_LINE_OR_CHANNEL_MUST_REMAIN_GEOMETRICALLY_VALID_UNTIL_ITS_ANCHORS_ARE_OBSERVABLE"
)

for _rule in (
    MEANINGFUL_HORIZONTAL_RULE,
    CHANNEL_FOURTH_POINT_RULE,
    OBSERVABLE_STRUCTURE_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class SourceFaithfulStructureBook(LifecycleAwareStructureBook):
    """Causal structure book with explicit human-to-program admission rules."""

    def _line_respected(self, first: Pivot, second: Pivot, side: str) -> bool:
        for bar in self.bars[first.index :]:
            expected = self._line_value(first, second, bar.ts_close_ns)
            if side == "LOW" and bar.low < expected - self.tick_size:
                return False
            if side == "HIGH" and bar.high > expected + self.tick_size:
                return False
        return True

    @staticmethod
    def expected_channel_edge(channel: Channel) -> str:
        return "UPPER" if channel.direction == "ASCENDING" else "LOWER"

    def is_expected_channel_boundary(self, source_structure_id: str) -> bool:
        channel = self.channel_for_boundary(source_structure_id)
        if channel is None:
            return False
        edge = source_structure_id.rsplit(":", 1)[-1]
        return edge == self.expected_channel_edge(channel)

    def _channel_respected(self, channel: Channel, start_index: int, end_index: int) -> bool:
        del end_index
        for bar in self.bars[start_index:]:
            lower = channel.lower_at(bar.ts_close_ns)
            upper = channel.upper_at(bar.ts_close_ns)
            if bar.low < lower - self.tick_size or bar.high > upper + self.tick_size:
                return False
        return True

    def _fourth_edge_touched_before_observation(self, channel: Channel, second: Pivot) -> bool:
        edge = self.expected_channel_edge(channel)
        for bar in self.bars[second.index + 1 :]:
            value = (
                channel.upper_at(bar.ts_close_ns)
                if edge == "UPPER"
                else channel.lower_at(bar.ts_close_ns)
            )
            touched = (
                bar.high >= value - self.tick_size
                if edge == "UPPER"
                else bar.low <= value + self.tick_size
            )
            if touched:
                return True
        return False

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
            observed_time_ns=max(
                first.observed_time_ns,
                second.observed_time_ns,
                opposite.observed_time_ns,
            ),
            pivot_span=min(first.span, second.span),
            strength_ratio=min(
                first.strength_ratio,
                second.strength_ratio,
                opposite.strength_ratio,
            ),
        )
        if not self._channel_respected(channel, first.index, len(self.bars) - 1):
            self._inc("channel_rejected_historical_violation")
            return None
        if self._fourth_edge_touched_before_observation(channel, second):
            self._inc("channel_rejected_fourth_point_before_observation")
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

    def _active_anchor_pivot_ids(self, time_ns: int) -> set[str]:
        output: set[str] = set()
        for line in self.active_trend_lines(time_ns):
            output.add(line.first_pivot_id)
            output.add(line.second_pivot_id)
        for channel in self.active_channels(time_ns):
            output.add(channel.main_first_pivot_id)
            output.add(channel.main_second_pivot_id)
            output.add(channel.opposite_pivot_id)
        return output

    def is_meaningful_horizontal(self, pivot: Pivot, time_ns: int) -> bool:
        if pivot.span == max(self.pivot_spans):
            return True
        return pivot.pivot_id in self._active_anchor_pivot_ids(time_ns)

    def boundaries_at(self, time_ns: int) -> list[StructureZone]:
        output = super().boundaries_at(time_ns)
        filtered: list[StructureZone] = []
        for zone in output:
            if zone.family is StructureFamily.HORIZONTAL:
                pivot = self.pivot_for_structure(zone.source_structure_id)
                if pivot is None or not self.is_meaningful_horizontal(pivot, time_ns):
                    continue
            if (
                zone.family is StructureFamily.CHANNEL
                and not self.is_expected_channel_boundary(zone.source_structure_id)
            ):
                continue
            filtered.append(zone)
        return filtered
