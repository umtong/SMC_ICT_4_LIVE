from __future__ import annotations

from decimal import Decimal
import unittest

from mtf_strategy_daily_risk_v11 import (
    DAILY_LOSS_CAP_FRACTION,
    DAILY_RISK_PROVENANCE,
    DailyRiskDayTradeStrategy,
    DailyRiskOpposingOrderBlockExitStrategy,
    unused_daily_loss_budget,
)
from mtf_strategy_day_v7 import EasyChartDayTradeStrategy
from mtf_strategy_exit_v9 import OpposingOrderBlockExitStrategy


class DailyRiskTests(unittest.TestCase):
    def test_one_percent_starting_nav_is_absolute_daily_allowance(self) -> None:
        self.assertEqual(
            unused_daily_loss_budget(
                Decimal("10000"),
                Decimal("10000"),
                Decimal("500"),
            ),
            Decimal("100"),
        )

    def test_realized_losses_consume_but_gains_do_not_expand_allowance(self) -> None:
        self.assertEqual(
            unused_daily_loss_budget(
                Decimal("10000"),
                Decimal("9940"),
                Decimal("100"),
            ),
            Decimal("40"),
        )
        self.assertEqual(
            unused_daily_loss_budget(
                Decimal("10000"),
                Decimal("10500"),
                Decimal("105"),
            ),
            Decimal("100"),
        )
        self.assertEqual(
            unused_daily_loss_budget(
                Decimal("10000"),
                Decimal("9899"),
                Decimal("100"),
            ),
            Decimal("0"),
        )

    def test_smaller_configured_trade_budget_is_preserved(self) -> None:
        self.assertEqual(
            unused_daily_loss_budget(
                Decimal("10000"),
                Decimal("10000"),
                Decimal("25"),
            ),
            Decimal("25"),
        )

    def test_invalid_budget_contract_fails_closed(self) -> None:
        for arguments in (
            (Decimal("0"), Decimal("100"), Decimal("1")),
            (Decimal("100"), Decimal("0"), Decimal("1")),
            (Decimal("100"), Decimal("100"), Decimal("-1")),
        ):
            with self.assertRaises(ValueError):
                unused_daily_loss_budget(*arguments)
        with self.assertRaises(ValueError):
            unused_daily_loss_budget(
                Decimal("100"),
                Decimal("100"),
                Decimal("1"),
                Decimal("1"),
            )

    def test_governor_composes_with_both_terminal_exit_policies(self) -> None:
        self.assertTrue(issubclass(DailyRiskDayTradeStrategy, EasyChartDayTradeStrategy))
        self.assertTrue(
            issubclass(
                DailyRiskOpposingOrderBlockExitStrategy,
                OpposingOrderBlockExitStrategy,
            ),
        )
        self.assertEqual(DAILY_LOSS_CAP_FRACTION, Decimal("0.01"))
        self.assertTrue(DAILY_RISK_PROVENANCE.startswith("SOURCE_EXPLICIT:"))


if __name__ == "__main__":
    unittest.main()
