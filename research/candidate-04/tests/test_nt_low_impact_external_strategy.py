from __future__ import annotations

import unittest

from nt_low_impact_external_strategy import choose_external_liquidity_target


class ExternalLiquidityTargetTests(unittest.TestCase):
    def test_long_selects_nearest_level_meeting_cost_floor(self) -> None:
        target = choose_external_liquidity_target(
            [("near", 101.0), ("valid", 103.0), ("far", 106.0)],
            entry=100.0,
            stop=98.0,
            side=1,
            cost_rate=0.0,
            minimum_net_r=1.2,
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.price, 103.0)
        self.assertEqual(target.source, "valid")

    def test_short_ignores_levels_behind_entry(self) -> None:
        target = choose_external_liquidity_target(
            [("behind", 102.0), ("near", 99.0), ("valid", 97.0)],
            entry=100.0,
            stop=102.0,
            side=-1,
            cost_rate=0.0,
            minimum_net_r=1.2,
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.price, 97.0)
        self.assertEqual(target.source, "valid")

    def test_returns_none_when_no_external_level_has_sufficient_distance(self) -> None:
        target = choose_external_liquidity_target(
            [("near", 100.5), ("behind", 99.0)],
            entry=100.0,
            stop=98.0,
            side=1,
            cost_rate=0.0,
            minimum_net_r=1.2,
        )
        self.assertIsNone(target)

    def test_duplicate_prices_keep_first_structural_source(self) -> None:
        target = choose_external_liquidity_target(
            [("30m", 103.0), ("60m", 103.0)],
            entry=100.0,
            stop=98.0,
            side=1,
            cost_rate=0.0,
            minimum_net_r=1.2,
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.source, "30m")


if __name__ == "__main__":
    unittest.main()
