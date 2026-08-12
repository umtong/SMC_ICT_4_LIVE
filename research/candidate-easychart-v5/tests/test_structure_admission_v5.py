from __future__ import annotations

import unittest

from contracts_v5 import Channel, ObjectKind, Pivot, TrendLine
from domain import Candle, Side
from easychart_zones import ZoneSide
from structure_admission_v5 import SourceFaithfulStructureBook

NS = 60_000_000_000


def candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(index * NS, open_, high, low, close, 1.0)


def pivot(
    pivot_id: str,
    side: str,
    price: float,
    index: int,
    observed_index: int,
    span: int,
) -> Pivot:
    return Pivot(
        pivot_id=pivot_id,
        side=side,
        price=price,
        index=index,
        event_time_ns=index * NS,
        observed_index=observed_index,
        observed_time_ns=observed_index * NS,
        span=span,
        strength_ratio=2.0,
    )


def register(book: SourceFaithfulStructureBook, *items: Pivot) -> None:
    book.pivots.extend(items)
    book._pivot_ids.update(item.pivot_id for item in items)
    book._active_pivots.update({item.pivot_id: item for item in items})


class SourceFaithfulAdmissionTests(unittest.TestCase):
    def test_local_pivot_is_targetable_but_not_automatically_trade_context(self) -> None:
        book = SourceFaithfulStructureBook("TEST", 15, 0.1, pivot_spans=(1, 3))
        local_high = pivot("LOCAL_HIGH", "HIGH", 110.0, 2, 3, 1)
        register(book, local_high)

        boundaries = book.boundaries_at(10 * NS)
        self.assertFalse(
            any(zone.source_structure_id == local_high.pivot_id for zone in boundaries),
        )
        target = book.target_for(
            Side.LONG,
            interaction_time_ns=10 * NS,
            source_span=1,
            current_high=105.0,
            current_low=100.0,
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target[1], 110.0)

    def test_structural_span_pivot_is_admitted_as_horizontal_context(self) -> None:
        book = SourceFaithfulStructureBook("TEST", 15, 0.1, pivot_spans=(1, 3))
        structural_low = pivot("STRUCTURAL_LOW", "LOW", 90.0, 2, 5, 3)
        register(book, structural_low)

        boundaries = book.boundaries_at(10 * NS)
        self.assertTrue(
            any(zone.source_structure_id == structural_low.pivot_id for zone in boundaries),
        )

    def test_local_pivot_anchoring_live_diagonal_is_meaningful_context(self) -> None:
        book = SourceFaithfulStructureBook("TEST", 15, 0.1, pivot_spans=(1, 3))
        first = pivot("L1", "LOW", 100.0, 1, 2, 1)
        second = pivot("L2", "LOW", 105.0, 5, 6, 1)
        register(book, first, second)
        line = TrendLine(
            structure_id="UP_LINE",
            kind=ObjectKind.UPTREND_LINE,
            side=ZoneSide.SUPPORT,
            timeframe_minutes=15,
            first_pivot_id=first.pivot_id,
            second_pivot_id=second.pivot_id,
            first_time_ns=first.event_time_ns,
            second_time_ns=second.event_time_ns,
            first_price=first.price,
            second_price=second.price,
            observed_time_ns=second.observed_time_ns,
            pivot_span=1,
            strength_ratio=2.0,
        )
        book.trend_lines.append(line)
        book._line_ids.add(line.structure_id)

        sources = {zone.source_structure_id for zone in book.boundaries_at(10 * NS)}
        self.assertIn(first.pivot_id, sources)
        self.assertIn(second.pivot_id, sources)
        self.assertIn(line.structure_id, sources)

    def test_channel_exposes_only_expected_fourth_edge_as_rotation_context(self) -> None:
        book = SourceFaithfulStructureBook("TEST", 60, 0.1, pivot_spans=(1, 3))
        channel = Channel(
            channel_id="ASC_CH",
            timeframe_minutes=60,
            direction="ASCENDING",
            main_first_pivot_id="L1",
            main_second_pivot_id="L2",
            opposite_pivot_id="H1",
            first_time_ns=0,
            second_time_ns=10 * NS,
            first_price=100.0,
            second_price=110.0,
            offset=10.0,
            observed_time_ns=12 * NS,
            pivot_span=3,
            strength_ratio=2.0,
        )
        book.channels.append(channel)
        book._channel_ids.add(channel.channel_id)

        sources = {zone.source_structure_id for zone in book.boundaries_at(13 * NS)}
        self.assertIn("ASC_CH:UPPER", sources)
        self.assertNotIn("ASC_CH:LOWER", sources)
        self.assertTrue(book.is_expected_channel_boundary("ASC_CH:UPPER"))
        self.assertFalse(book.is_expected_channel_boundary("ASC_CH:LOWER"))

    def test_line_broken_while_second_anchor_is_becoming_observable_is_rejected(self) -> None:
        book = SourceFaithfulStructureBook("TEST", 60, 0.1, pivot_spans=(2,))
        book.bars = [
            candle(0, 103.0, 104.0, 102.0, 103.0),
            candle(1, 101.0, 103.0, 100.0, 102.0),
            candle(2, 103.0, 105.0, 102.0, 104.0),
            candle(3, 105.0, 107.0, 104.0, 106.0),
            candle(4, 107.0, 108.0, 106.0, 107.0),
            candle(5, 106.0, 107.0, 105.0, 106.0),
            candle(6, 106.0, 107.0, 105.4, 106.5),
            candle(7, 106.5, 108.0, 106.0, 107.5),
        ]
        first = pivot("L1", "LOW", 100.0, 1, 3, 2)
        second = pivot("L2", "LOW", 105.0, 5, 7, 2)
        register(book, first, second)

        self.assertIsNone(book._build_trend_line(second))

    def test_channel_whose_fourth_edge_arrived_before_observation_is_not_fresh(self) -> None:
        book = SourceFaithfulStructureBook("TEST", 60, 0.1, pivot_spans=(2,))
        book.bars = [
            candle(0, 100.0, 101.0, 99.0, 100.0),
            candle(1, 100.0, 101.0, 100.0, 100.5),
            candle(2, 100.5, 102.0, 100.0, 101.5),
            candle(3, 105.0, 110.0, 104.0, 109.0),
            candle(4, 108.0, 109.0, 106.0, 107.0),
            candle(5, 106.0, 107.0, 105.0, 106.0),
            candle(6, 112.0, 113.2, 111.0, 112.5),
            candle(7, 111.5, 112.0, 110.5, 111.0),
        ]
        second = pivot("L2", "LOW", 105.0, 5, 7, 2)
        channel = Channel(
            channel_id="ASC_EARLY",
            timeframe_minutes=60,
            direction="ASCENDING",
            main_first_pivot_id="L1",
            main_second_pivot_id=second.pivot_id,
            opposite_pivot_id="H1",
            first_time_ns=1 * NS,
            second_time_ns=5 * NS,
            first_price=100.0,
            second_price=105.0,
            offset=5.0,
            observed_time_ns=7 * NS,
            pivot_span=2,
            strength_ratio=2.0,
        )
        self.assertTrue(book._fourth_edge_touched_before_observation(channel, second))


if __name__ == "__main__":
    unittest.main()
