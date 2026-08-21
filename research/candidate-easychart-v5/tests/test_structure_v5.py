from __future__ import annotations

import unittest

from contracts_v5 import ObjectKind, TrendLine
from domain import Candle
from easychart_zones import ZoneSide
from structure_v5 import CausalStructureBook

NS = 60_000_000_000


def bar(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(index * NS, open_, high, low, close, 1.0)


class CausalStructureTests(unittest.TestCase):
    def test_pivot_is_not_observable_until_right_side_closes(self) -> None:
        book = CausalStructureBook("TEST", 60, 0.1, pivot_spans=(1,))
        book.on_bar(bar(0, 105, 107, 104, 106))
        book.on_bar(bar(1, 103, 106, 100, 104))
        self.assertFalse(book.pivots)
        book.on_bar(bar(2, 104, 108, 103, 107))
        lows = [pivot for pivot in book.pivots if pivot.side == "LOW"]
        self.assertEqual(len(lows), 1)
        self.assertEqual(lows[0].index, 1)
        self.assertEqual(lows[0].observed_index, 2)
        self.assertEqual(lows[0].observed_time_ns, 2 * NS)

    def test_parallel_channel_requires_three_confirmed_wick_pivots(self) -> None:
        book = CausalStructureBook("TEST", 60, 0.1, pivot_spans=(1,))
        bars = [
            bar(0, 106, 107, 105, 106),
            bar(1, 104, 106, 103, 105),
            bar(2, 103, 104, 100, 102),
            bar(3, 105, 108, 104, 107),
            bar(4, 108, 110, 106, 109),
            bar(5, 107, 108, 105, 106),
            bar(6, 105, 107, 104, 106),
            bar(7, 107, 109, 106, 108),
        ]
        for item in bars[:-1]:
            book.on_bar(item)
        self.assertFalse(book.channels)
        book.on_bar(bars[-1])
        self.assertEqual(len(book.channels), 1)
        channel = book.channels[0]
        self.assertEqual(channel.direction, "ASCENDING")
        self.assertEqual(channel.observed_time_ns, bars[-1].ts_close_ns)
        later = 8 * NS
        lower = channel.lower_at(later)
        upper = channel.upper_at(later)
        self.assertGreater(upper, lower)
        self.assertAlmostEqual(upper - lower, channel.offset)

    def test_diagonal_structure_is_projected_at_later_retest_time(self) -> None:
        book = CausalStructureBook("TEST", 60, 0.1, pivot_spans=(1,))
        line = TrendLine(
            structure_id="UP_LINE",
            kind=ObjectKind.UPTREND_LINE,
            side=ZoneSide.SUPPORT,
            timeframe_minutes=60,
            first_pivot_id="L1",
            second_pivot_id="L2",
            first_time_ns=0,
            second_time_ns=10 * NS,
            first_price=100.0,
            second_price=110.0,
            observed_time_ns=10 * NS,
            pivot_span=1,
            strength_ratio=2.0,
        )
        book.trend_lines.append(line)
        original = book._line_snapshot(line, 11 * NS)
        projected = book.snapshot_for(original, 20 * NS)
        self.assertGreater(projected.lower, original.upper)
        self.assertAlmostEqual((projected.lower + projected.upper) / 2.0, 120.0)


if __name__ == "__main__":
    unittest.main()
