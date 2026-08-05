from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "research" / "candidate-01"
SRC = ROOT / "src"
for item in (CANDIDATE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_data import AggTrade
from aggtrade_time_clock import NS_PER_MINUTE, iter_time_bars


def trade(
    agg_id: int,
    *,
    minute: int,
    second: int,
    price: float,
    quantity: float,
    buyer_maker: bool,
) -> AggTrade:
    return AggTrade(
        agg_trade_id=agg_id,
        price=price,
        quantity=quantity,
        first_trade_id=agg_id,
        last_trade_id=agg_id,
        ts_event_ns=minute * NS_PER_MINUTE + second * 1_000_000_000,
        is_buyer_maker=buyer_maker,
    )


class AggTradeTimeClockTest(unittest.TestCase):
    def test_completed_utc_buckets_preserve_ohlc_and_signed_flow(self) -> None:
        values = [
            trade(1, minute=0, second=1, price=100.0, quantity=1.0, buyer_maker=False),
            trade(2, minute=4, second=59, price=102.0, quantity=2.0, buyer_maker=True),
            trade(3, minute=5, second=0, price=101.0, quantity=1.5, buyer_maker=False),
            trade(4, minute=9, second=59, price=103.0, quantity=0.5, buyer_maker=False),
            # First event of bucket 2 proves bucket 1 is complete.
            trade(5, minute=10, second=0, price=104.0, quantity=1.0, buyer_maker=False),
        ]
        bars = list(iter_time_bars(values, interval_minutes=5))
        self.assertEqual(len(bars), 2)
        first, second = bars
        self.assertEqual((first.start_time_ns, first.end_time_ns), (0, 5 * NS_PER_MINUTE))
        self.assertEqual((first.open, first.high, first.low, first.close), (100.0, 102.0, 100.0, 102.0))
        self.assertAlmostEqual(first.quote_notional, 304.0)
        self.assertAlmostEqual(first.signed_quote_notional, 100.0 - 204.0)
        self.assertEqual(first.aggregate_trades, 2)
        self.assertEqual((second.open, second.close), (101.0, 103.0))
        self.assertEqual((second.first_agg_trade_id, second.last_agg_trade_id), (3, 4))

    def test_future_bucket_cannot_change_already_completed_bar(self) -> None:
        prefix = [
            trade(1, minute=0, second=1, price=100.0, quantity=1.0, buyer_maker=False),
            trade(2, minute=4, second=59, price=102.0, quantity=1.0, buyer_maker=True),
            trade(3, minute=5, second=0, price=101.0, quantity=1.0, buyer_maker=False),
        ]
        baseline = list(iter_time_bars(prefix, interval_minutes=5))[0]
        extended = [
            *prefix,
            trade(4, minute=5, second=1, price=500.0, quantity=1.0, buyer_maker=False),
            trade(5, minute=10, second=0, price=104.0, quantity=1.0, buyer_maker=False),
        ]
        observed = list(iter_time_bars(extended, interval_minutes=5))[0]
        self.assertEqual(baseline, observed)

    def test_non_monotonic_ids_are_rejected(self) -> None:
        values = [
            trade(2, minute=0, second=1, price=100.0, quantity=1.0, buyer_maker=False),
            trade(1, minute=0, second=2, price=101.0, quantity=1.0, buyer_maker=False),
        ]
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            list(iter_time_bars(values, interval_minutes=5, include_partial=True))


if __name__ == "__main__":
    unittest.main()
