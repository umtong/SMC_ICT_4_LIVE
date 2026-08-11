from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import Candle, Side
from market_v4 import (
    EasyChartStructuralEpisodeEngine,
    ParallelChannel,
    ScenarioConfigV4,
    StructuralPivot,
    strict_fvg_side,
)


NS = 60_000_000_000


def bar(index, open_, high, low, close, minutes=5):
    start = index * minutes * NS
    return Candle(start, start + minutes * NS - 1, open_, high, low, close, 1.0)


def pivot(index, side, level, observed=None, minutes=15):
    observed = index if observed is None else observed
    return StructuralPivot(
        center_index=index,
        observed_index=observed,
        side=side,
        level=level,
        event_time_ns=(index + 1) * minutes * NS - 1,
        observed_time_ns=(observed + 1) * minutes * NS - 1,
    )


def horizontal_long_channel():
    p1 = StructuralPivot(0, 0, "HIGH", 110.0, 0, 0)
    p2 = StructuralPivot(1, 1, "LOW", 100.0, 1, 1)
    p3 = StructuralPivot(2, 2, "HIGH", 110.0, 2, 2)
    return ParallelChannel(
        channel_id="ch",
        observed_time_ns=2,
        timeframe_minutes=15,
        anchor_side="HIGH",
        expected_side=Side.LONG,
        base_time_ns=0,
        base_level=110.0,
        slope_per_ns=0.0,
        width=10.0,
        p1=p1,
        p2=p2,
        p3=p3,
    )


class TestChannelGeometry(unittest.TestCase):
    def test_three_alternating_wick_pivots_create_exact_parallel_channel(self):
        candles = [
            bar(0, 107.0, 110.0, 106.0, 108.0, 15),
            bar(1, 104.0, 106.0, 100.0, 101.0, 15),
            bar(2, 105.0, 108.0, 104.0, 106.0, 15),
        ]
        p1 = pivot(0, "HIGH", 110.0, 0)
        p2 = pivot(1, "LOW", 100.0, 1)
        p3 = pivot(2, "HIGH", 108.0, 2)
        engine = EasyChartStructuralEpisodeEngine(
            "BTCUSDT",
            ScenarioConfigV4(min_body_ratio=1.0, min_previous_body_atr=0.0),
        )
        engine.structure_pivots = [p1, p2, p3]
        channel = engine._latest_channel(candles, p3)
        self.assertIsNotNone(channel)
        assert channel is not None
        self.assertEqual(channel.expected_side, Side.LONG)
        for time_ns in (p1.event_time_ns, p2.event_time_ns, p3.event_time_ns, p3.event_time_ns + 10 * NS):
            self.assertAlmostEqual(channel.upper(time_ns) - channel.lower(time_ns), channel.width)


class TestStructuralEpisode(unittest.TestCase):
    def engine(self, **overrides):
        config = ScenarioConfigV4(
            min_body_ratio=1.0,
            min_previous_body_atr=0.0,
            require_fvg=False,
            **overrides,
        )
        engine = EasyChartStructuralEpisodeEngine("BTCUSDT", config)
        engine.active_channel = horizontal_long_channel()
        engine.micro_high = StructuralPivot(0, 0, "HIGH", 103.0, 0, 1)
        return engine

    def test_point_four_fakeout_ob_then_bos_arms_one_fixed_target(self):
        engine = self.engine()
        candles = [
            bar(1, 101.0, 101.5, 100.0, 100.5),
            bar(2, 100.4, 101.5, 99.0, 101.2),
            bar(3, 101.3, 104.5, 101.3, 104.0),
        ]
        self.assertEqual(engine.on_five_minute_close(candles, 1), [])
        setups = engine.on_five_minute_close(candles, 2)
        self.assertEqual(len(setups), 1)
        setup = setups[0]
        self.assertEqual(setup.family, "CHANNEL_POINT4_FAKEOUT_OB_BOS")
        self.assertEqual(setup.side, Side.LONG)
        self.assertAlmostEqual(setup.target, 110.0)
        self.assertGreaterEqual(setup.executable(setup.target, target_id="x").gross_rr, 1.0)

    def test_outside_close_then_next_reclaim_is_trap_episode(self):
        engine = self.engine()
        candles = [
            bar(1, 101.0, 101.2, 100.5, 100.8),
            bar(2, 100.4, 100.6, 99.0, 99.5),
            bar(3, 99.5, 101.0, 99.2, 100.5),
        ]
        engine.on_five_minute_close(candles, 1)
        self.assertIsNotNone(engine.outside)
        engine.on_five_minute_close(candles, 2)
        self.assertIsNone(engine.outside)
        self.assertEqual(len(engine.episodes), 1)
        self.assertEqual(engine.episodes[0].family_prefix, "CHANNEL_POINT4_TRAP_RECLAIM")

    def test_plain_touch_is_not_traded_unless_explicitly_enabled(self):
        engine = self.engine()
        candles = [
            bar(1, 101.0, 101.2, 100.4, 100.8),
            bar(2, 100.7, 101.2, 100.0, 101.0),
        ]
        engine.on_five_minute_close(candles, 1)
        self.assertEqual(len(engine.episodes), 0)

        enabled = self.engine(enable_boundary_touch=True)
        enabled.on_five_minute_close(candles, 1)
        self.assertEqual(len(enabled.episodes), 1)
        self.assertEqual(enabled.episodes[0].family_prefix, "CHANNEL_POINT4_TOUCH")

    def test_ob_mitigated_before_bos_is_not_reused(self):
        engine = self.engine()
        candles = [
            bar(1, 101.0, 101.5, 100.0, 100.5),
            bar(2, 100.4, 101.5, 99.0, 101.2),
            # Re-enters the OB body before any close above BOS.
            bar(3, 101.1, 102.0, 100.7, 101.5),
            # Later BOS must not resurrect the already mitigated origin.
            bar(4, 101.6, 104.5, 101.4, 104.0),
        ]
        engine.on_five_minute_close(candles, 1)
        engine.on_five_minute_close(candles, 2)
        setups = engine.on_five_minute_close(candles, 3)
        self.assertEqual(setups, [])
        self.assertEqual(engine.diagnostics.get("bos_without_origin_ob"), 1)


class TestStrictFVG(unittest.TestCase):
    def test_requires_wick_gap_direction_and_large_middle_body(self):
        bullish = [
            bar(0, 100.0, 101.0, 99.5, 100.5),
            bar(1, 100.5, 105.0, 100.4, 104.5),
            bar(2, 102.0, 103.0, 102.0, 102.5),
        ]
        self.assertEqual(strict_fvg_side(bullish, 2, minimum_middle_body_ratio=2.0), Side.LONG)
        no_gap = [bullish[0], bullish[1], bar(2, 100.8, 103.0, 100.8, 102.5)]
        self.assertIsNone(strict_fvg_side(no_gap, 2, minimum_middle_body_ratio=2.0))


if __name__ == "__main__":
    unittest.main()
