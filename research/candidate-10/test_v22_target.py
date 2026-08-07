from __future__ import annotations

from dataclasses import dataclass
import unittest

from c10_v22_target import select_external_target


@dataclass
class Pool:
    pool_id: str
    side: str
    price: float
    source: str
    consumed: bool = False
    reserved: bool = False


class ExternalTargetTests(unittest.TestCase):
    def test_long_ignores_nearer_internal_pivot(self) -> None:
        pools = [
            Pool("pivot-high", "HIGH", 101.0, "CONFIRMED_PIVOT"),
            Pool("session-high-near", "HIGH", 105.0, "FUNDING_SESSION"),
            Pool("session-high-far", "HIGH", 110.0, "FUNDING_SESSION"),
        ]
        result = select_external_target(
            pools,
            direction=1,
            entry=100.0,
            source_pool_id="source",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.pool_id, "session-high-near")

    def test_short_ignores_nearer_internal_pivot(self) -> None:
        pools = [
            Pool("pivot-low", "LOW", 99.0, "CONFIRMED_PIVOT"),
            Pool("session-low-near", "LOW", 95.0, "FUNDING_SESSION"),
            Pool("session-low-far", "LOW", 90.0, "FUNDING_SESSION"),
        ]
        result = select_external_target(
            pools,
            direction=-1,
            entry=100.0,
            source_pool_id="source",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.pool_id, "session-low-near")

    def test_consumed_reserved_and_source_pools_are_excluded(self) -> None:
        pools = [
            Pool("source", "HIGH", 103.0, "FUNDING_SESSION"),
            Pool("consumed", "HIGH", 104.0, "FUNDING_SESSION", consumed=True),
            Pool("reserved", "HIGH", 105.0, "FUNDING_SESSION", reserved=True),
            Pool("valid", "HIGH", 106.0, "FUNDING_SESSION"),
        ]
        result = select_external_target(
            pools,
            direction=1,
            entry=100.0,
            source_pool_id="source",
        )
        self.assertEqual(result.pool_id, "valid")

    def test_no_external_pool_returns_none(self) -> None:
        result = select_external_target(
            [Pool("pivot", "HIGH", 101.0, "CONFIRMED_PIVOT")],
            direction=1,
            entry=100.0,
            source_pool_id="source",
        )
        self.assertIsNone(result)

    def test_invalid_direction_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            select_external_target([], direction=0, entry=100.0, source_pool_id="source")


if __name__ == "__main__":
    unittest.main()
