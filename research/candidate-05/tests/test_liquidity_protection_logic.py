from __future__ import annotations

import unittest
from dataclasses import dataclass

from flow_inflection_logic import choose_protectable_liquidity_milestone


@dataclass(frozen=True)
class Level:
    pool_id: str
    kind: str
    level: float


class LiquidityProtectionLogicTest(unittest.TestCase):
    def test_nearest_pool_with_positive_protected_net_is_selected(self) -> None:
        pools = [
            Level("too-close", "HIGH", 101.0),
            Level("protectable", "HIGH", 102.0),
            Level("later", "HIGH", 104.0),
        ]
        result = choose_protectable_liquidity_milestone(
            entry=100.0,
            target=105.0,
            side=1,
            pools=pools,
            atr=2.0,
            stop_buffer_atr=0.08,
            cost_rate=0.005,
            adverse_slippage_rate=0.001,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[0], "protectable")
        self.assertAlmostEqual(result[1], 102.0)
        self.assertAlmostEqual(result[2], 101.84)
        self.assertGreater(result[3], 0.0)

    def test_long_and_short_selection_are_mirror_symmetric(self) -> None:
        long_result = choose_protectable_liquidity_milestone(
            entry=100.0,
            target=110.0,
            side=1,
            pools=[Level("long", "HIGH", 103.0)],
            atr=2.0,
            stop_buffer_atr=0.08,
            cost_rate=0.00075,
            adverse_slippage_rate=0.00025,
        )
        short_result = choose_protectable_liquidity_milestone(
            entry=100.0,
            target=90.0,
            side=-1,
            pools=[Level("short", "LOW", 97.0)],
            atr=2.0,
            stop_buffer_atr=0.08,
            cost_rate=0.00075,
            adverse_slippage_rate=0.00025,
        )
        self.assertIsNotNone(long_result)
        self.assertIsNotNone(short_result)
        assert long_result is not None and short_result is not None
        self.assertAlmostEqual(long_result[2] + short_result[2], 200.0, places=9)
        self.assertGreater(long_result[3], 0.0)
        self.assertGreater(short_result[3], 0.0)

    def test_final_target_is_not_an_intermediate_milestone(self) -> None:
        result = choose_protectable_liquidity_milestone(
            entry=100.0,
            target=103.0,
            side=1,
            pools=[Level("target", "HIGH", 103.0)],
            atr=2.0,
            stop_buffer_atr=0.08,
            cost_rate=0.00075,
            adverse_slippage_rate=0.00025,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
