from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
import unittest

from continuous_aggregate import (
    ClosedTrade,
    consecutive_empty_weeks,
    realized_drawdown,
    weekly_path,
    wilson_interval,
)


class ContinuousAggregateTests(unittest.TestCase):
    def trade(self, day: int, pnl: str, *, close_hour: int = 1) -> ClosedTrade:
        opened = datetime(2026, 5, 11 + day, 0, 0, tzinfo=UTC)
        closed = datetime(2026, 5, 11 + day, close_hour, 0, tzinfo=UTC)
        return ClosedTrade(opened, closed, Decimal(pnl), "BTCUSDT-PERP.BINANCE")

    def test_eight_of_eight_is_not_precise_eighty_percent_evidence(self) -> None:
        low, high = wilson_interval(8, 8)
        self.assertLess(low, 0.80)
        self.assertGreater(high, 0.99)

    def test_sixteen_of_sixteen_reaches_eighty_percent_wilson_floor(self) -> None:
        low, _ = wilson_interval(16, 16)
        self.assertGreaterEqual(low, 0.80)

    def test_continuous_drawdown_crosses_calendar_week_boundary(self) -> None:
        trades = [
            self.trade(0, "10000"),
            self.trade(8, "-20000"),
        ]
        drawdown = realized_drawdown(Decimal("100000"), trades)
        self.assertAlmostEqual(drawdown, 20_000 / 110_000)

    def test_weekly_path_is_slice_of_one_compounding_account(self) -> None:
        trades = [
            self.trade(0, "10000"),
            self.trade(8, "-5000"),
        ]
        weeks = weekly_path(
            starting_nav=Decimal("100000"),
            start=date(2026, 5, 11),
            end_exclusive=date(2026, 5, 25),
            trades=trades,
        )
        self.assertEqual(len(weeks), 2)
        self.assertEqual(weeks[0]["starting_realized_nav"], "100000")
        self.assertEqual(weeks[0]["ending_realized_nav"], "110000")
        self.assertEqual(weeks[1]["starting_realized_nav"], "110000")
        self.assertEqual(weeks[1]["ending_realized_nav"], "105000")

    def test_final_flatten_at_end_exclusive_belongs_to_last_week(self) -> None:
        trade = ClosedTrade(
            datetime(2026, 5, 24, 23, 0, tzinfo=UTC),
            datetime(2026, 5, 25, 0, 0, tzinfo=UTC),
            Decimal("2500"),
            "BTCUSDT-PERP.BINANCE",
        )
        weeks = weekly_path(
            starting_nav=Decimal("100000"),
            start=date(2026, 5, 11),
            end_exclusive=date(2026, 5, 25),
            trades=[trade],
        )
        self.assertEqual(weeks[-1]["closed_trades"], 1)

    def test_empty_week_streak(self) -> None:
        weekly = [
            {"closed_trades": 1},
            {"closed_trades": 0},
            {"closed_trades": 0},
            {"closed_trades": 1},
            {"closed_trades": 0},
        ]
        self.assertEqual(consecutive_empty_weeks(weekly), 2)


if __name__ == "__main__":
    unittest.main()
