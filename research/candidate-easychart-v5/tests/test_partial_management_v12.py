from __future__ import annotations

from decimal import Decimal
import unittest

from partial_management_smoke_v12 import split_half


class PartialManagementTests(unittest.TestCase):
    def test_exact_even_split(self) -> None:
        self.assertEqual(
            split_half(Decimal("1.000"), Decimal("0.001")),
            (Decimal("0.500"), Decimal("0.500")),
        )

    def test_odd_increment_keeps_total_quantity(self) -> None:
        first, remainder = split_half(Decimal("1.001"), Decimal("0.001"))
        self.assertEqual(first, Decimal("0.500"))
        self.assertEqual(remainder, Decimal("0.501"))
        self.assertEqual(first + remainder, Decimal("1.001"))

    def test_unsplittable_quantity_fails_closed(self) -> None:
        for quantity, increment in (
            (Decimal("0"), Decimal("0.001")),
            (Decimal("1"), Decimal("0")),
            (Decimal("0.001"), Decimal("0.001")),
        ):
            with self.assertRaises(ValueError):
                split_half(quantity, increment)


if __name__ == "__main__":
    unittest.main()
