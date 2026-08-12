from __future__ import annotations

import unittest

from nautilus_trader.model.enums import OrderSide, PositionSide

from mtf_strategy_day_v7 import (
    MAX_HOLD_HOURS,
    MAX_HOLD_NS,
    MAX_HOLD_PROVENANCE,
    active_timer_names,
    closing_order_side,
    max_hold_deadline_ns,
)


class PropertyClock:
    timer_names = ["alpha", "beta"]


class MethodClock:
    def timer_names(self) -> list[str]:
        return ["gamma", "delta"]


class DayTradeLifecycleTests(unittest.TestCase):
    def test_deadline_is_exactly_one_day_after_first_real_fill(self) -> None:
        first_fill = 1_700_000_000_000_000_000
        self.assertEqual(MAX_HOLD_HOURS, 24)
        self.assertEqual(max_hold_deadline_ns(first_fill) - first_fill, MAX_HOLD_NS)
        self.assertEqual(MAX_HOLD_NS, 86_400_000_000_000)

    def test_negative_fill_timestamp_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            max_hold_deadline_ns(-1)

    def test_full_exit_side_is_opposite_the_open_position(self) -> None:
        self.assertIs(closing_order_side(PositionSide.LONG), OrderSide.SELL)
        self.assertIs(closing_order_side(PositionSide.SHORT), OrderSide.BUY)
        with self.assertRaises(ValueError):
            closing_order_side(PositionSide.FLAT)

    def test_timer_names_supports_pinned_property_and_newer_method_surfaces(self) -> None:
        self.assertEqual(active_timer_names(PropertyClock()), ("alpha", "beta"))
        self.assertEqual(active_timer_names(MethodClock()), ("gamma", "delta"))

    def test_rule_is_explicitly_source_provenanced(self) -> None:
        self.assertTrue(MAX_HOLD_PROVENANCE.startswith("SOURCE_EXPLICIT:"))
        self.assertIn("DAY_TRADING", MAX_HOLD_PROVENANCE)


if __name__ == "__main__":
    unittest.main()
