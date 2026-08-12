from __future__ import annotations

from decimal import Decimal
import unittest

from mtf_strategy_daily_risk_v11 import (
    DAILY_LOSS_CAP_FRACTION,
    DAILY_RISK_PROVENANCE,
    DailyRiskDayTradeStrategy,
    DailyRiskOpposingOrderBlockExitStrategy,
    realized_loss_amount,
    unused_daily_loss_budget,
)
from mtf_strategy_day_v7 import EasyChartDayTradeStrategy
from mtf_strategy_exit_v9 import OpposingOrderBlockExitStrategy


class FakeMoney:
    def __init__(self, value: float) -> None:
        self.value = value

    def as_double(self) -> float:
        return self.value


class DailyRiskTests(unittest.TestCase):
    def test_one_percent_starting_nav_is_absolute_daily_allowance(self) -> None:
        self.assertEqual(
            unused_daily_loss_budget(
                Decimal("10000"),
                Decimal("0"),
                Decimal("500"),
            ),
            Decimal("100"),
        )

    def test_realized_losses_consume_and_gains_cannot_restore_allowance(self) -> None:
        self.assertEqual(
            unused_daily_loss_budget(
                Decimal("10000"),
                Decimal("60"),
                Decimal("100"),
            ),
            Decimal("40"),
        )
        # A prior gain is not an input to the gross-loss budget. The same sixty
        # dollars of realized losing trades leaves the same forty dollars.
        self.assertEqual(
            unused_daily_loss_budget(
                Decimal("10000"),
                Decimal("60"),
                Decimal("105"),
            ),
            Decimal("40"),
        )
        self.assertEqual(
            unused_daily_loss_budget(
                Decimal("10000"),
                Decimal("101"),
                Decimal("100"),
            ),
            Decimal("0"),
        )

    def test_smaller_configured_trade_budget_is_preserved(self) -> None:
        self.assertEqual(
            unused_daily_loss_budget(
                Decimal("10000"),
                Decimal("0"),
                Decimal("25"),
            ),
            Decimal("25"),
        )

    def test_realized_money_contributes_only_when_trade_is_losing(self) -> None:
        self.assertEqual(realized_loss_amount(None), Decimal("0"))
        self.assertEqual(realized_loss_amount(FakeMoney(50.0)), Decimal("0"))
        self.assertEqual(realized_loss_amount(FakeMoney(-37.5)), Decimal("37.5"))

    def test_invalid_budget_contract_fails_closed(self) -> None:
        for arguments in (
            (Decimal("0"), Decimal("0"), Decimal("1")),
            (Decimal("100"), Decimal("-1"), Decimal("1")),
            (Decimal("100"), Decimal("0"), Decimal("-1")),
        ):
            with self.assertRaises(ValueError):
                unused_daily_loss_budget(*arguments)
        with self.assertRaises(ValueError):
            unused_daily_loss_budget(
                Decimal("100"),
                Decimal("0"),
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
