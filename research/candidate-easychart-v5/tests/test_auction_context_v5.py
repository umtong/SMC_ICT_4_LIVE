from __future__ import annotations

import math
import unittest

from auction_context_v5 import (
    AuctionState,
    CausalAuctionContext,
    ContextPivot,
)
from domain import Candle

NS = 60_000_000_000


def candle(
    index: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1.0,
) -> Candle:
    return Candle(index * NS, open_, high, low, close, volume)


def pivot(side: str, price: float, index: int, span: int = 2) -> ContextPivot:
    return ContextPivot(
        side=side,
        price=price,
        event_time_ns=index * NS,
        observed_time_ns=(index + span) * NS,
        span=span,
        index=index,
    )


class CausalAuctionContextTests(unittest.TestCase):
    def test_pivot_is_unavailable_until_right_side_span_closes(self) -> None:
        context = CausalAuctionContext("TEST", 15, pivot_spans=(2, 6))
        bars = [
            candle(0, 100.0, 101.0, 99.0, 100.0),
            candle(1, 100.0, 102.0, 99.5, 101.0),
            candle(2, 101.0, 105.0, 100.0, 104.0),
            candle(3, 104.0, 104.5, 101.0, 102.0),
            candle(4, 102.0, 103.0, 100.5, 101.0),
        ]
        for bar in bars[:4]:
            context.on_bar(bar)
        self.assertFalse(any(item.index == 2 for item in context.pivots[2]))
        context.on_bar(bars[4])
        confirmed = [item for item in context.pivots[2] if item.index == 2]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0].observed_time_ns, bars[4].ts_close_ns)

    def test_higher_high_and_higher_low_are_up_context(self) -> None:
        context = CausalAuctionContext("TEST", 15, pivot_spans=(2, 6))
        context.pivots[2] = [
            pivot("HIGH", 100.0, 2),
            pivot("LOW", 90.0, 4),
            pivot("HIGH", 110.0, 6),
            pivot("LOW", 95.0, 8),
        ]
        self.assertIs(context.state(2), AuctionState.UP)

    def test_lower_high_and_lower_low_are_down_context(self) -> None:
        context = CausalAuctionContext("TEST", 15, pivot_spans=(2, 6))
        context.pivots[2] = [
            pivot("HIGH", 110.0, 2),
            pivot("LOW", 95.0, 4),
            pivot("HIGH", 105.0, 6),
            pivot("LOW", 90.0, 8),
        ]
        self.assertIs(context.state(2), AuctionState.DOWN)

    def test_mixed_high_low_progression_is_transition(self) -> None:
        context = CausalAuctionContext("TEST", 15, pivot_spans=(2, 6))
        context.pivots[2] = [
            pivot("HIGH", 100.0, 2),
            pivot("LOW", 90.0, 4),
            pivot("HIGH", 105.0, 6),
            pivot("LOW", 85.0, 8),
        ]
        self.assertIs(context.state(2), AuctionState.TRANSITION)

    def test_rolling_24h_fields_use_only_completed_observations(self) -> None:
        context = CausalAuctionContext("TEST", 60, pivot_spans=(2, 6))
        for index in range(25):
            close = 100.0 + index
            context.on_bar(
                candle(
                    index,
                    close - 0.25,
                    close + 1.0,
                    close - 1.0,
                    close,
                    volume=2.0,
                ),
            )
        snapshot = context.snapshot()
        expected_return = 124.0 / 100.0 - 1.0
        expected_notional = sum((100.0 + index) * 2.0 for index in range(1, 25))
        self.assertTrue(math.isclose(snapshot.return_24h or 0.0, expected_return))
        self.assertTrue(
            math.isclose(snapshot.notional_volume_24h or 0.0, expected_notional),
        )
        self.assertGreaterEqual(snapshot.range_position_24h or -1.0, 0.0)
        self.assertLessEqual(snapshot.range_position_24h or 2.0, 1.0)
        self.assertEqual(snapshot.observed_time_ns, 24 * NS)


if __name__ == "__main__":
    unittest.main()
