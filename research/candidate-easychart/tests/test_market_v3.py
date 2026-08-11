from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import Candle, LiquidityPool, Side, TargetMode
from market_v3 import EasyChartScenarioEngine, PivotConfirmation, ScenarioConfig, confirmed_pivot


class TestMarketV3(unittest.TestCase):
    @staticmethod
    def pool(pool_id: str, side: str, level: float, timeframe: int = 5):
        return LiquidityPool(pool_id, side, level, 1, 2, timeframe, 2 if timeframe >= 15 else 1)

    def test_sweep_ob_arms_dynamic_impulse_target(self):
        engine = EasyChartScenarioEngine(
            "BTCUSDT",
            ScenarioConfig(enable_sweep_ob=True, enable_break_ob=False, tick_size=0.1),
        )
        engine.active_pools = {"low": self.pool("low", "LOW", 100.0)}
        bars = [
            Candle(10, 19, 102.0, 102.5, 100.5, 101.0),
            Candle(20, 29, 100.8, 103.0, 99.0, 102.2),
        ]
        setups = engine.on_five_minute_close(bars, 1)
        self.assertEqual(len(setups), 1)
        setup = setups[0]
        self.assertEqual(setup.family, "SWEEP_RECLAIM_OB")
        self.assertEqual(setup.target_mode, TargetMode.IMPULSE_EXTREME)
        self.assertEqual(setup.initial_target, 103.0)
        self.assertEqual(setup.entry, 102.0)
        self.assertEqual(setup.stop, 98.9)

    def test_direct_sweep_uses_paired_opposite_structure(self):
        engine = EasyChartScenarioEngine(
            "BTCUSDT",
            ScenarioConfig(
                enable_sweep_ob=False,
                enable_break_ob=False,
                enable_direct_sweep=True,
                tick_size=0.1,
            ),
        )
        engine.active_pools = {
            "low": self.pool("low", "LOW", 100.0, 15),
            "high": self.pool("high", "HIGH", 110.0, 15),
        }
        bars = [
            Candle(10, 19, 102.0, 102.5, 100.5, 101.0),
            Candle(20, 29, 101.0, 103.0, 99.0, 102.0),
        ]
        setups = engine.on_five_minute_close(bars, 1)
        self.assertEqual(len(setups), 1)
        setup = setups[0]
        self.assertEqual(setup.family, "SWEEP_RECLAIM_RETEST")
        self.assertEqual(setup.target_mode, TargetMode.FIXED_STRUCTURE)
        self.assertEqual(setup.initial_target, 110.0)

    def test_nested_pools_crossed_in_one_bar_are_one_episode(self):
        engine = EasyChartScenarioEngine(
            "BTCUSDT",
            ScenarioConfig(
                enable_sweep_ob=False,
                enable_break_ob=False,
                enable_direct_break=True,
                tick_size=0.1,
            ),
        )
        engine.active_pools = {
            "h1": self.pool("h1", "HIGH", 100.0, 5),
            "h2": self.pool("h2", "HIGH", 101.0, 15),
        }
        bars = [
            Candle(10, 19, 99.0, 99.5, 98.5, 99.0),
            Candle(20, 29, 99.0, 103.0, 98.8, 102.0),
        ]
        setups = engine.on_five_minute_close(bars, 1)
        self.assertEqual(len(setups), 1)
        self.assertEqual(setups[0].source_pool_id, "h2")
        self.assertEqual(engine.diagnostics["nested_break_pool_collapsed"], 1)

    def test_context_pivot_is_causal(self):
        bars = [
            Candle(i * 10, i * 10 + 9, 10.0, high, 8.0, 9.0)
            for i, high in enumerate([10, 12, 15, 12, 10])
        ]
        self.assertIsNone(confirmed_pivot(bars, 3, 2))
        pivot = confirmed_pivot(bars, 4, 2)
        self.assertEqual(pivot.center_index, 2)
        self.assertEqual(pivot.observed_index, 4)

    def test_context_router_rejects_counterdirection(self):
        engine = EasyChartScenarioEngine("BTCUSDT", ScenarioConfig(require_htf_alignment=True))
        engine.add_context_pivot(PivotConfirmation(0, 1, "HIGH", 100.0))
        engine.add_context_pivot(PivotConfirmation(1, 2, "LOW", 90.0))
        engine.add_context_pivot(PivotConfirmation(2, 3, "HIGH", 110.0))
        engine.add_context_pivot(PivotConfirmation(3, 4, "LOW", 95.0))
        self.assertEqual(engine.context_bias, "BULL")
        self.assertTrue(engine._side_allowed(Side.LONG))
        self.assertFalse(engine._side_allowed(Side.SHORT))


if __name__ == "__main__":
    unittest.main()
