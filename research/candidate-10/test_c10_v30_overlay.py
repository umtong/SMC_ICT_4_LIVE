from __future__ import annotations

from dataclasses import dataclass
import unittest

from c10_v30_overlay import (
    cost_neutral_stop,
    equilibrium_reached,
    source_midpoint,
)


class SourceEquilibriumLifecycleTest(unittest.TestCase):
    def test_long_cost_neutral_stop_offsets_declared_costs(self):
        entry = 100.0
        maker = 0.0004
        taker = 0.0008
        impact = 0.07
        stop = cost_neutral_stop(
            direction="LONG",
            entry_price=entry,
            maker_fee=maker,
            taker_fee=taker,
            impact_per_side=impact,
        )
        modeled_pnl = (
            stop - entry
            - entry * maker
            - stop * taker
            - 2.0 * impact
        )
        self.assertAlmostEqual(modeled_pnl, 0.0, places=10)
        self.assertGreater(stop, entry)

    def test_short_cost_neutral_stop_offsets_declared_costs(self):
        entry = 100.0
        maker = 0.0004
        taker = 0.0008
        impact = 0.07
        stop = cost_neutral_stop(
            direction="SHORT",
            entry_price=entry,
            maker_fee=maker,
            taker_fee=taker,
            impact_per_side=impact,
        )
        modeled_pnl = (
            entry - stop
            - entry * maker
            - stop * taker
            - 2.0 * impact
        )
        self.assertAlmostEqual(modeled_pnl, 0.0, places=10)
        self.assertLess(stop, entry)

    def test_equilibrium_is_directional_and_causal(self):
        self.assertTrue(
            equilibrium_reached(
                direction="LONG",
                high=101.0,
                low=99.0,
                midpoint=100.5,
            ),
        )
        self.assertFalse(
            equilibrium_reached(
                direction="LONG",
                high=100.4,
                low=99.0,
                midpoint=100.5,
            ),
        )
        self.assertTrue(
            equilibrium_reached(
                direction="SHORT",
                high=101.0,
                low=99.0,
                midpoint=99.5,
            ),
        )

    def test_source_midpoint_uses_preexisting_paired_range(self):
        @dataclass
        class Pool:
            scenario_id: str
            level: float
            opposite_level: float | None

        class Logic:
            pools = [Pool("S1", 120.0, 80.0)]

        self.assertEqual(source_midpoint(Logic(), "S1"), 100.0)
        self.assertIsNone(source_midpoint(Logic(), "MISSING"))


if __name__ == "__main__":
    unittest.main()
