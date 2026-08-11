from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import Candle, Side
from market_v4 import StructuralPivot
from market_v8 import (
    EasyChartLiquidityPoolEngine,
    LiquidityPool,
    PoolDetectorConfig,
    PoolInteractionState,
    PoolTrapConfig,
    WickLiquidityPoolDetector,
)


NS = 60_000_000_000


def bar(index, open_, high, low, close, minutes=5):
    start = index * minutes * NS
    return Candle(start, start + minutes * NS - 1, open_, high, low, close, 1.0)


def bull_pool(observed=1):
    return LiquidityPool(
        pool_id="bull",
        symbol="BTCUSDT",
        side=Side.LONG,
        observed_time_ns=observed,
        origin_time_ns=0,
        origin_index=0,
        zone_low=99.0,
        zone_high=100.0,
        contacts=2,
        source_timeframe_minutes=5,
    )


def bear_pool(observed=1):
    return LiquidityPool(
        pool_id="bear",
        symbol="BTCUSDT",
        side=Side.SHORT,
        observed_time_ns=observed,
        origin_time_ns=0,
        origin_index=0,
        zone_low=109.0,
        zone_high=110.0,
        contacts=2,
        source_timeframe_minutes=5,
    )


class TestWickLiquidityPoolDetector(unittest.TestCase):
    def test_repeated_separated_wicks_form_causal_bull_pool(self):
        detector = WickLiquidityPoolDetector(
            "BTCUSDT",
            PoolDetectorConfig(
                contact_count=2,
                gap_bars=2,
                confirmation_bars=2,
                mitigation_closes=2,
                source_timeframe_minutes=5,
            ),
        )
        candles = [
            # Reference low: body bottom 100, outer wick 99.
            bar(0, 101.0, 102.0, 99.0, 100.0),
            bar(1, 101.0, 102.0, 100.2, 101.5),
            # Separated revisit: wick through 100, body stays above it.
            bar(2, 101.0, 102.0, 98.8, 100.5),
            bar(3, 100.5, 102.0, 100.1, 101.5),
            bar(4, 101.5, 103.0, 101.0, 102.5),
        ]
        formed = []
        for index, candle in enumerate(candles):
            formed.extend(detector.on_candle(candle, index).formed)
        self.assertEqual(len(formed), 1)
        pool = formed[0]
        self.assertEqual(pool.side, Side.LONG)
        self.assertEqual(pool.zone_low, 98.8)
        self.assertEqual(pool.zone_high, 100.0)
        self.assertEqual(pool.contacts, 2)
        self.assertEqual(pool.observed_time_ns, candles[4].ts_close_ns)

    def test_two_consecutive_body_closes_through_outer_edge_mitigate(self):
        detector = WickLiquidityPoolDetector(
            "BTCUSDT",
            PoolDetectorConfig(contact_count=2, gap_bars=1, confirmation_bars=1),
        )
        pool = bull_pool()
        detector.active[pool.pool_id] = pool
        detector.break_counts[pool.pool_id] = 0
        self.assertEqual(detector.on_candle(bar(1, 100.0, 100.5, 98.0, 98.5), 1).mitigated, ())
        update = detector.on_candle(bar(2, 98.5, 99.0, 97.0, 98.0), 2)
        self.assertEqual(update.mitigated, (pool,))
        self.assertNotIn(pool.pool_id, detector.active)


class TestLiquidityPoolEpisodes(unittest.TestCase):
    def engine(self, **overrides):
        detector = PoolDetectorConfig(
            contact_count=2,
            gap_bars=2,
            confirmation_bars=2,
            mitigation_closes=2,
            source_timeframe_minutes=5,
        )
        values = {
            "detector": detector,
            "enable_immediate_fakeout": True,
            "enable_delayed_trap": True,
            "tick_size": 0.1,
        }
        values.update(overrides)
        engine = EasyChartLiquidityPoolEngine("BTCUSDT", PoolTrapConfig(**values))
        engine.detector.active["bull"] = bull_pool()
        engine.detector.break_counts["bull"] = 0
        engine.detector.active["bear"] = bear_pool()
        engine.detector.break_counts["bear"] = 0
        engine.states["bull"] = PoolInteractionState(bull_pool())
        engine.states["bear"] = PoolInteractionState(bear_pool())
        return engine

    def test_immediate_fakeout_targets_nearest_opposing_pool(self):
        engine = self.engine(enable_delayed_trap=False)
        setups = engine.on_candle(bar(1, 101.0, 102.0, 98.0, 101.0), 1)
        long_setups = [setup for setup in setups if setup.side is Side.LONG]
        self.assertEqual(len(long_setups), 1)
        setup = long_setups[0]
        self.assertEqual(setup.entry, 100.0)
        self.assertEqual(setup.stop, 97.9)
        self.assertEqual(setup.initial_target, 109.0)
        self.assertIn("IMMEDIATE_FAKEOUT_RETEST", setup.family)
        self.assertIsNotNone(setup.executable(setup.initial_target, target_id=setup.fixed_target_id))

    def test_delayed_outside_close_then_reclaim_uses_deepest_extreme(self):
        engine = self.engine(enable_immediate_fakeout=False)
        self.assertEqual(engine.on_candle(bar(1, 100.5, 101.0, 98.0, 98.5), 1), [])
        self.assertEqual(engine.on_candle(bar(2, 98.5, 99.0, 97.0, 98.0), 2), [])
        setups = engine.on_candle(bar(3, 98.0, 101.0, 97.5, 100.5), 3)
        long_setups = [setup for setup in setups if setup.side is Side.LONG]
        self.assertEqual(len(long_setups), 1)
        self.assertEqual(long_setups[0].stop, 96.9)
        self.assertIn("DELAYED_TRAP_RETEST", long_setups[0].family)

    def test_confirmed_structural_high_is_objective_fallback(self):
        engine = self.engine(enable_delayed_trap=False)
        engine.detector.active.pop("bear")
        engine.detector.break_counts.pop("bear")
        engine.states.pop("bear")
        engine.add_structural_pivot(
            StructuralPivot(
                center_index=0,
                observed_index=0,
                side="HIGH",
                level=107.0,
                event_time_ns=0,
                observed_time_ns=1,
            ),
        )
        setups = engine.on_candle(bar(1, 101.0, 102.0, 98.0, 101.0), 1)
        self.assertEqual(len(setups), 1)
        self.assertEqual(setups[0].initial_target, 107.0)
        self.assertTrue(str(setups[0].fixed_target_id).startswith("STRUCTURAL_HIGH"))

    def test_missing_opposing_objective_consumes_event_without_trade(self):
        engine = self.engine(enable_delayed_trap=False)
        engine.detector.active.pop("bear")
        engine.detector.break_counts.pop("bear")
        engine.states.pop("bear")
        setups = engine.on_candle(bar(1, 101.0, 102.0, 98.0, 101.0), 1)
        self.assertEqual(setups, [])
        self.assertEqual(engine.diagnostics.get("no_opposing_objective"), 1)
        self.assertNotIn("bull", engine.states)

    def test_first_objective_below_one_r_is_rejected_not_skipped(self):
        engine = self.engine(enable_delayed_trap=False)
        close_bear = LiquidityPool(
            pool_id="near-bear",
            symbol="BTCUSDT",
            side=Side.SHORT,
            observed_time_ns=1,
            origin_time_ns=0,
            origin_index=0,
            zone_low=101.0,
            zone_high=102.0,
            contacts=2,
            source_timeframe_minutes=5,
        )
        engine.detector.active.pop("bear")
        engine.detector.break_counts.pop("bear")
        engine.states.pop("bear")
        engine.detector.active[close_bear.pool_id] = close_bear
        engine.detector.break_counts[close_bear.pool_id] = 0
        engine.states[close_bear.pool_id] = PoolInteractionState(close_bear)
        setups = engine.on_candle(bar(1, 101.0, 101.5, 97.0, 100.5), 1)
        self.assertEqual(setups, [])
        self.assertEqual(engine.diagnostics.get("gross_rr_lt_1"), 1)


if __name__ == "__main__":
    unittest.main()
