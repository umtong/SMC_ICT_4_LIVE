from __future__ import annotations

from decimal import Decimal
import unittest

from c10_v27_overlay import CostAwareRiskSizer, LiveImpactLedger


class CostAwareSizingTest(unittest.TestCase):
    def solve(self, liquidity: float):
        sizer = CostAwareRiskSizer(0.03)
        sizer.set_context(atr=100.0, liquidity_notional=liquidity, tick_size=0.1)
        decision = sizer.size(
            nav=Decimal("100000"),
            loss_per_unit=Decimal("500"),
            entry_price=Decimal("50000"),
            quantity_increment=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("5"),
            margin_init=Decimal("0.05"),
            free_balance=Decimal("100000"),
        )
        return sizer, decision

    def test_shallow_liquidity_reduces_quantity(self):
        deep_sizer, deep = self.solve(1_000_000_000.0)
        shallow_sizer, shallow = self.solve(10_000_000.0)
        self.assertTrue(deep.feasible)
        self.assertTrue(shallow.feasible)
        self.assertLess(shallow.quantity, deep.quantity)
        self.assertGreater(
            shallow_sizer.last_solution.impact_per_side,
            deep_sizer.last_solution.impact_per_side,
        )

    def test_total_planned_loss_never_exceeds_three_percent(self):
        _, decision = self.solve(50_000_000.0)
        self.assertLessEqual(
            decision.expected_total_loss,
            decision.planned_loss_budget + Decimal("0.01"),
        )

    def test_live_ledger_reduces_next_nav(self):
        ledger = LiveImpactLedger()
        cost = ledger.debit(quantity=Decimal("10"), impact_per_unit=Decimal("2.5"))
        self.assertEqual(cost, Decimal("25.0"))
        self.assertEqual(
            ledger.conservative_equity(Decimal("100000")),
            Decimal("99975.0"),
        )


if __name__ == "__main__":
    unittest.main()
