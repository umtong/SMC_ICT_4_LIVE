from __future__ import annotations

import unittest

from cost_geometry_v5 import conservative_after_cost_target


class AfterCostTargetGeometryTests(unittest.TestCase):
    def test_tight_long_target_can_be_gross_positive_but_after_cost_negative(self) -> None:
        result = conservative_after_cost_target(
            is_long=True,
            entry=100.0,
            target=100.08,
            price_increment=0.01,
            entry_slippage_ticks=2,
            entry_fee_rate=0.00075,
            target_fee_rate=0.00075,
            funding_rate=0.00010,
        )
        self.assertGreater(result.target, result.entry)
        self.assertLessEqual(result.net_target_per_unit, 0)
        self.assertFalse(result.positive)

    def test_tight_short_target_is_symmetric(self) -> None:
        result = conservative_after_cost_target(
            is_long=False,
            entry=100.0,
            target=99.92,
            price_increment=0.01,
            entry_slippage_ticks=2,
            entry_fee_rate=0.00075,
            target_fee_rate=0.00075,
            funding_rate=0.00010,
        )
        self.assertLess(result.target, result.entry)
        self.assertLessEqual(result.net_target_per_unit, 0)
        self.assertFalse(result.positive)

    def test_ordinary_long_target_remains_positive(self) -> None:
        result = conservative_after_cost_target(
            is_long=True,
            entry=100.0,
            target=102.0,
            price_increment=0.01,
            entry_slippage_ticks=2,
            entry_fee_rate=0.00075,
            target_fee_rate=0.00075,
            funding_rate=0.00010,
        )
        self.assertTrue(result.positive)
        self.assertGreater(result.net_target_per_unit, 1.0)

    def test_no_unconfigured_target_slippage_is_invented(self) -> None:
        result = conservative_after_cost_target(
            is_long=True,
            entry=10.0,
            target=11.0,
            price_increment=0.1,
            entry_slippage_ticks=2,
            entry_fee_rate=0.0,
            target_fee_rate=0.0,
            funding_rate=0.0,
        )
        self.assertEqual(float(result.conservative_entry_fill), 10.2)
        self.assertEqual(float(result.gross_target_per_unit), 0.8)
        self.assertEqual(float(result.net_target_per_unit), 0.8)

    def test_negative_cost_rate_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            conservative_after_cost_target(
                is_long=True,
                entry=100.0,
                target=101.0,
                price_increment=0.01,
                entry_slippage_ticks=2,
                entry_fee_rate=-0.001,
                target_fee_rate=0.0,
                funding_rate=0.0,
            )


if __name__ == "__main__":
    unittest.main()
