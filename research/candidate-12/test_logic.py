from __future__ import annotations

from decimal import Decimal
import unittest

from logic import BarObs, CausalLiquidityAuctionEngine, LogicConfig, RiskSizer

NS_MINUTE = 60_000_000_000


class RiskSizerTests(unittest.TestCase):
    def test_three_percent_budget_is_not_exceeded_after_rounding(self) -> None:
        decision = RiskSizer(0.03).size(
            nav=Decimal("100000"),
            loss_per_unit=Decimal("83.17"),
            entry_price=Decimal("30000"),
            quantity_increment=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("10"),
            margin_init=Decimal("0.05"),
            free_balance=Decimal("100000"),
        )
        self.assertTrue(decision.feasible)
        self.assertLessEqual(decision.expected_total_loss, Decimal("3000"))

    def test_margin_is_a_feasibility_check_not_a_risk_multiplier(self) -> None:
        decision = RiskSizer(0.03).size(
            nav=Decimal("100000"),
            loss_per_unit=Decimal("10"),
            entry_price=Decimal("100000"),
            quantity_increment=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("10"),
            margin_init=Decimal("0.05"),
            free_balance=Decimal("100"),
        )
        self.assertFalse(decision.feasible)
        self.assertEqual(decision.reason, "INSUFFICIENT_MARGIN")


class LogicTests(unittest.TestCase):
    def test_bar_rejects_impossible_ohlc(self) -> None:
        with self.assertRaises(ValueError):
            BarObs(1, 100, 99, 98, 100, 1, 0.5)

    def test_risk_fraction_above_three_percent_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LogicConfig(risk_fraction=0.031)

    def test_completed_asia_range_is_not_frozen_early(self) -> None:
        engine = CausalLiquidityAuctionEngine(LogicConfig(atr_period=2), "BTCUSDT-PERP.BINANCE")
        # Feed 1-minute completed bars through 05:59 UTC.  The 00:00-06:00
        # session is not complete and cannot be used as a liquidity source.
        for minute in range(1, 360):
            price = 100 + minute * 0.001
            engine.on_bar(BarObs(minute * NS_MINUTE, price, price + 1, price - 1, price, 10, 5))
        self.assertFalse(any(event.event_type == "SESSION_RANGE_FROZEN" for event in engine.events))
        # The 06:00 close is finalized only when the next minute arrives.
        engine.on_bar(BarObs(360 * NS_MINUTE, 100, 101, 99, 100, 10, 5))
        engine.on_bar(BarObs(361 * NS_MINUTE, 100, 101, 99, 100, 10, 5))
        frozen = [event for event in engine.events if event.event_type == "SESSION_RANGE_FROZEN"]
        self.assertEqual(len(frozen), 1)
        self.assertEqual(frozen[0].observed_time_ns, 360 * NS_MINUTE)
        self.assertGreaterEqual(frozen[0].observed_time_ns, frozen[0].event_time_ns)


if __name__ == "__main__":
    unittest.main()
