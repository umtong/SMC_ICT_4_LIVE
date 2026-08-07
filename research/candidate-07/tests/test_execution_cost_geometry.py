from __future__ import annotations

from decimal import Decimal
import unittest

from execution_cost_geometry import adverse_execution_geometry


class AdverseExecutionGeometryTests(unittest.TestCase):
    def test_long_and_short_are_symmetric_without_directional_fee_notional(self) -> None:
        common = {
            "price_increment": Decimal("0.1"),
            "taker_fee_rate": Decimal("0"),
            "funding_reserve_bps": Decimal("0"),
        }
        long = adverse_execution_geometry(
            direction="LONG",
            entry_reference=Decimal("100"),
            stop_price=Decimal("99"),
            target_price=Decimal("102"),
            **common,
        )
        short = adverse_execution_geometry(
            direction="SHORT",
            entry_reference=Decimal("100"),
            stop_price=Decimal("101"),
            target_price=Decimal("98"),
            **common,
        )
        self.assertEqual(long.per_unit_expected_loss, short.per_unit_expected_loss)
        self.assertEqual(
            long.per_unit_expected_target_gain,
            short.per_unit_expected_target_gain,
        )
        self.assertTrue(long.target_is_net_positive)
        self.assertTrue(short.target_is_net_positive)

    def test_nominally_favorable_target_can_be_net_negative(self) -> None:
        geometry = adverse_execution_geometry(
            direction="LONG",
            entry_reference=Decimal("100000"),
            stop_price=Decimal("99980"),
            target_price=Decimal("100020"),
            price_increment=Decimal("0.1"),
            taker_fee_rate=Decimal("0.00018"),
            funding_reserve_bps=Decimal("1.5"),
        )
        self.assertLess(geometry.per_unit_expected_target_gain, 0)
        self.assertFalse(geometry.target_is_net_positive)

    def test_economically_reachable_target_passes_without_tuned_r_threshold(self) -> None:
        geometry = adverse_execution_geometry(
            direction="SHORT",
            entry_reference=Decimal("100000"),
            stop_price=Decimal("100020"),
            target_price=Decimal("99900"),
            price_increment=Decimal("0.1"),
            taker_fee_rate=Decimal("0.00018"),
            funding_reserve_bps=Decimal("1.5"),
        )
        self.assertGreater(geometry.per_unit_expected_target_gain, 0)
        self.assertTrue(geometry.target_is_net_positive)

    def test_funding_reserve_reduces_target_gain(self) -> None:
        base = dict(
            direction="LONG",
            entry_reference=Decimal("1000"),
            stop_price=Decimal("990"),
            target_price=Decimal("1020"),
            price_increment=Decimal("0.1"),
            taker_fee_rate=Decimal("0.0002"),
        )
        zero = adverse_execution_geometry(
            **base,
            funding_reserve_bps=Decimal("0"),
        )
        reserved = adverse_execution_geometry(
            **base,
            funding_reserve_bps=Decimal("2"),
        )
        self.assertLess(
            reserved.per_unit_expected_target_gain,
            zero.per_unit_expected_target_gain,
        )


if __name__ == "__main__":
    unittest.main()
