from __future__ import annotations

from decimal import Decimal
import unittest

from c10_v32_overlay import solve_funded_reduction


class FundedPartialRiskTransferTest(unittest.TestCase):
    def solve(self, direction: str = "LONG"):
        return solve_funded_reduction(
            direction=direction,
            total_quantity=Decimal("10"),
            entry_price=Decimal("100"),
            current_price=Decimal("104") if direction == "LONG" else Decimal("96"),
            original_loss_per_unit=Decimal("3"),
            maker_fee=Decimal("0.0004"),
            taker_fee=Decimal("0.0008"),
            impact_per_side=Decimal("0.05"),
            tick_size=Decimal("0.1"),
            quantity_increment=Decimal("0.1"),
            min_quantity=Decimal("0.1"),
        )

    def test_long_partial_profit_funds_residual_original_stop(self):
        result = self.solve("LONG")
        self.assertIsNotNone(result)
        self.assertGreater(result.partial_quantity, 0)
        self.assertGreater(result.residual_quantity, 0)
        self.assertGreaterEqual(
            result.locked_profit + Decimal("0.01"),
            result.residual_max_loss,
        )
        self.assertLess(result.partial_quantity, Decimal("10"))

    def test_short_partial_profit_funds_residual_original_stop(self):
        result = self.solve("SHORT")
        self.assertIsNotNone(result)
        self.assertGreaterEqual(
            result.locked_profit + Decimal("0.01"),
            result.residual_max_loss,
        )

    def test_fraction_is_solved_not_fixed(self):
        first = self.solve("LONG")
        second = solve_funded_reduction(
            direction="LONG",
            total_quantity=Decimal("10"),
            entry_price=Decimal("100"),
            current_price=Decimal("110"),
            original_loss_per_unit=Decimal("3"),
            maker_fee=Decimal("0.0004"),
            taker_fee=Decimal("0.0008"),
            impact_per_side=Decimal("0.05"),
            tick_size=Decimal("0.1"),
            quantity_increment=Decimal("0.1"),
            min_quantity=Decimal("0.1"),
        )
        self.assertLess(second.fraction, first.fraction)

    def test_no_reduction_before_cost_after_profit_exists(self):
        result = solve_funded_reduction(
            direction="LONG",
            total_quantity=Decimal("10"),
            entry_price=Decimal("100"),
            current_price=Decimal("100.1"),
            original_loss_per_unit=Decimal("3"),
            maker_fee=Decimal("0.0004"),
            taker_fee=Decimal("0.0008"),
            impact_per_side=Decimal("0.05"),
            tick_size=Decimal("0.1"),
            quantity_increment=Decimal("0.1"),
            min_quantity=Decimal("0.1"),
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
