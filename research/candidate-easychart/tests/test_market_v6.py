from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import Candle, Side
from market_v4 import StructuralPivot
from market_v5 import ScenarioConfigV5
from market_v6 import (
    DirectionalLiquidityRange,
    EasyChartDirectionalLiquidityEngine,
)


NS = 60_000_000_000


def bar(index, open_, high, low, close, minutes=5):
    start = index * minutes * NS
    return Candle(start, start + minutes * NS - 1, open_, high, low, close, 1.0)


def pivot(index, side, level, observed=None):
    observed = index if observed is None else observed
    return StructuralPivot(
        center_index=index,
        observed_index=observed,
        side=side,
        level=level,
        event_time_ns=(index + 1) * 15 * NS - 1,
        observed_time_ns=(observed + 1) * 15 * NS - 1,
    )


def liquidity_range():
    high = StructuralPivot(0, 0, "HIGH", 110.0, 0, 1)
    low = StructuralPivot(1, 1, "LOW", 100.0, 1, 2)
    return DirectionalLiquidityRange(
        range_id="range",
        observed_time_ns=2,
        high=high,
        low=low,
    )


class TestDirectionalLiquidityRange(unittest.TestCase):
    def engine(self, **overrides):
        config = ScenarioConfigV5(
            min_body_ratio=1.0,
            min_previous_body_atr=0.0,
            enable_immediate_fakeout=True,
            enable_one_bar_trap=True,
            **overrides,
        )
        engine = EasyChartDirectionalLiquidityEngine("BTCUSDT", config)
        engine.micro_high = StructuralPivot(0, 0, "HIGH", 103.0, 0, 1)
        engine.micro_low = StructuralPivot(0, 0, "LOW", 107.0, 0, 1)
        return engine

    def test_latest_alternating_dc_high_low_define_dealing_range(self):
        engine = self.engine()
        engine.add_directional_context_pivot(pivot(1, "HIGH", 110.0, 2))
        self.assertIsNone(engine.active_liquidity_range)
        engine.add_directional_context_pivot(pivot(3, "LOW", 100.0, 4))
        current = engine.active_liquidity_range
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.upper, 110.0)
        self.assertEqual(current.lower, 100.0)
        self.assertEqual(current.width, 10.0)

    def test_immediate_sweep_ob_and_bos_arm_opposite_range_target(self):
        engine = self.engine(enable_one_bar_trap=False)
        engine.active_liquidity_range = liquidity_range()
        candles = [
            bar(1, 101.0, 101.5, 100.0, 100.5),
            bar(2, 100.4, 101.5, 99.0, 101.2),
            bar(3, 101.3, 104.5, 101.3, 104.0),
        ]
        self.assertEqual(engine.on_five_minute_close(candles, 1), [])
        setups = engine.on_five_minute_close(candles, 2)
        self.assertEqual(len(setups), 1)
        setup = setups[0]
        self.assertEqual(setup.family, "DC_SWING_FAKEOUT_OB_BOS")
        self.assertEqual(setup.side, Side.LONG)
        self.assertAlmostEqual(setup.initial_target, 110.0)

    def test_delayed_reclaim_creates_trap_not_accepted_break(self):
        engine = self.engine(enable_immediate_fakeout=False)
        engine.active_liquidity_range = liquidity_range()
        engine._observe_channel_interaction(bar(1, 100.5, 100.7, 99.0, 99.5), 1)
        engine._observe_channel_interaction(bar(2, 99.5, 100.0, 97.5, 98.5), 2)
        self.assertIsNotNone(engine.range_excursion)
        engine._observe_channel_interaction(bar(3, 98.5, 101.0, 98.0, 100.5), 3)
        self.assertIsNone(engine.range_excursion)
        self.assertEqual(len(engine.episodes), 1)
        self.assertEqual(engine.episodes[0].family_prefix, "DC_SWING_DELAYED_TRAP")
        self.assertEqual(engine.episodes[0].interaction_extreme, 97.5)

    def test_one_full_range_continuation_retires_boundary(self):
        engine = self.engine(enable_immediate_fakeout=False)
        engine.active_liquidity_range = liquidity_range()
        engine._observe_channel_interaction(bar(1, 100.5, 100.7, 99.0, 99.5), 1)
        engine._observe_channel_interaction(bar(2, 99.5, 99.8, 89.0, 90.0), 2)
        self.assertIsNone(engine.active_liquidity_range)
        self.assertIsNone(engine.range_excursion)
        self.assertEqual(len(engine.episodes), 0)
        self.assertEqual(engine.diagnostics.get("dc_range_accepted_break_full_width"), 1)


if __name__ == "__main__":
    unittest.main()
