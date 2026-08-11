from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain import Candle, LiquidityPool, Side
from market import EasyChartScenarioEngine, PivotConfirmation, ScenarioConfig, confirmed_pivot


class TestMarket(unittest.TestCase):
    def test_pivot_is_observed_only_after_right_span(self):
        bars = []
        lows = [10, 9, 8, 9, 10]
        for i, low in enumerate(lows):
            bars.append(Candle(i * 10, i * 10 + 9, 11, 12, low, 11))
        self.assertIsNone(confirmed_pivot(bars, 3, 2))
        pivot = confirmed_pivot(bars, 4, 2)
        self.assertIsNotNone(pivot)
        self.assertEqual(pivot.center_index, 2)
        self.assertEqual(pivot.observed_index, 4)
        self.assertEqual(pivot.side, "LOW")

    @staticmethod
    def pool(pool_id, side, level):
        return LiquidityPool(pool_id, side, level, 1, 2, 5)

    def test_same_bar_sweep_reclaim_and_body_engulf_produces_first_retest_plan(self):
        engine = EasyChartScenarioEngine("BTCUSDT", ScenarioConfig(tick_size=0.1))
        engine.active_pools = {
            "sell": self.pool("sell", "LOW", 100),
            "buy": self.pool("buy", "HIGH", 110),
        }
        candles = [
            Candle(10, 19, 102, 102.5, 100.5, 101),
            Candle(20, 29, 100.8, 102.4, 99, 102.2),
        ]
        plans = engine.on_five_minute_close(candles, 1)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.family, "SWEEP_RECLAIM_OB")
        self.assertEqual(plan.side, Side.LONG)
        self.assertEqual(plan.entry, 102)
        self.assertEqual(plan.stop, 98.9)
        self.assertEqual(plan.target, 110)
        self.assertGreaterEqual(plan.gross_rr, 1)
        self.assertEqual(plan.observed_time_ns, 29)

    def test_break_plan_uses_broken_level_and_ob_invalidation(self):
        engine = EasyChartScenarioEngine("BTCUSDT", ScenarioConfig(tick_size=0.1))
        engine.active_pools = {
            "break": self.pool("break", "HIGH", 100),
            "target": self.pool("target", "HIGH", 110),
        }
        candles = [
            Candle(10, 19, 99.5, 100, 98.8, 99),
            Candle(20, 29, 98.9, 102, 98.5, 101.5),
        ]
        plans = engine.on_five_minute_close(candles, 1)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.family, "BREAK_ACCEPT_RETEST_OB")
        self.assertEqual(plan.entry, 100)
        self.assertEqual(plan.stop, 98.4)
        self.assertEqual(plan.target, 110)

    def test_target_crossed_by_confirmation_bar_is_not_reused(self):
        engine = EasyChartScenarioEngine("BTCUSDT", ScenarioConfig(tick_size=0.1, enable_break_retest=False))
        engine.active_pools = {
            "sell": self.pool("sell", "LOW", 100),
            "already": self.pool("already", "HIGH", 102),
        }
        candles = [
            Candle(10, 19, 102, 102.2, 100.5, 101),
            Candle(20, 29, 100.8, 103, 99, 102.2),
        ]
        plans = engine.on_five_minute_close(candles, 1)
        self.assertEqual(plans, [])
        self.assertEqual(engine.diagnostics["no_first_structural_target"], 1)

    def test_direct_reclaim_retest_does_not_require_order_block(self):
        engine = EasyChartScenarioEngine(
            "BTCUSDT",
            ScenarioConfig(
                tick_size=0.1,
                enable_sweep_reversal=False,
                enable_break_retest=False,
                enable_direct_sweep_retest=True,
            ),
        )
        engine.active_pools = {
            "sell": self.pool("sell", "LOW", 100),
            "buy": self.pool("buy", "HIGH", 110),
        }
        bars = [
            Candle(10, 19, 101, 101.5, 100.5, 101),
            Candle(20, 29, 101, 102, 99, 100.5),
        ]
        plans = engine.on_five_minute_close(bars, 1)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.family, "SWEEP_RECLAIM_RETEST")
        self.assertEqual(plan.entry, 100)
        self.assertEqual(plan.stop, 98.9)
        self.assertEqual(plan.target, 110)

    def test_direct_break_first_retest_does_not_require_order_block(self):
        engine = EasyChartScenarioEngine(
            "BTCUSDT",
            ScenarioConfig(
                tick_size=0.1,
                enable_sweep_reversal=False,
                enable_break_retest=False,
                enable_direct_break_retest=True,
            ),
        )
        engine.active_pools = {
            "break": self.pool("break", "HIGH", 100),
            "target": self.pool("target", "HIGH", 110),
        }
        bars = [
            Candle(10, 19, 99.5, 99.8, 98.8, 99),
            Candle(20, 29, 99, 102, 98.5, 101.5),
        ]
        plans = engine.on_five_minute_close(bars, 1)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.family, "BREAK_ACCEPT_RETEST")
        self.assertEqual(plan.entry, 100)
        self.assertEqual(plan.stop, 98.4)
        self.assertEqual(plan.target, 110)

    def test_causal_hourly_structure_router_builds_bull_bias(self):
        engine = EasyChartScenarioEngine("BTCUSDT", ScenarioConfig(require_htf_alignment=True))
        engine.add_context_pivot(PivotConfirmation(0, 2, "HIGH", 100))
        engine.add_context_pivot(PivotConfirmation(1, 3, "LOW", 90))
        engine.add_context_pivot(PivotConfirmation(2, 4, "HIGH", 110))
        engine.add_context_pivot(PivotConfirmation(3, 5, "LOW", 95))
        self.assertEqual(engine.context_bias, "BULL")
        self.assertTrue(engine._side_allowed(Side.LONG))
        self.assertFalse(engine._side_allowed(Side.SHORT))

    def test_htf_router_blocks_countertrend_sweep(self):
        engine = EasyChartScenarioEngine(
            "BTCUSDT",
            ScenarioConfig(
                tick_size=0.1,
                require_htf_alignment=True,
                enable_break_retest=False,
            ),
        )
        engine.context_bias = "BULL"
        engine.active_pools = {
            "buy": self.pool("buy", "HIGH", 100),
            "sell": self.pool("sell", "LOW", 90),
        }
        bars = [
            Candle(10, 19, 98, 99.5, 97.5, 99),
            Candle(20, 29, 99.2, 101, 96, 97.8),
        ]
        self.assertEqual(engine.on_five_minute_close(bars, 1), [])
        self.assertGreater(engine.diagnostics["htf_rejected_BULL_SHORT"], 0)

if __name__ == "__main__":
    unittest.main()
