from __future__ import annotations

from datetime import datetime, timezone
import unittest

from hierarchical_sweep_engine import HierarchicalLiquiditySweepContinuationEngine, _AuctionBar
from lrb_types import BarObservation, PrimitiveSnapshot


def ns(hour: int, minute: int) -> int:
    return int(datetime(2024, 2, 26, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def snap(index, timestamp, open_, high, low, close, flow=0.0, volume=100.0):
    width = max(high - low, 0.1)
    return PrimitiveSnapshot(
        index,
        BarObservation(
            timestamp,
            open_,
            high,
            low,
            close,
            volume,
            volume * (flow + 1.0) / 2.0,
            10,
        ),
        True,
        1.0,
        1.5,
        flow,
        abs(close - open_),
        width,
        max(high - max(open_, close), 0.0) / width,
        max(min(open_, close) - low, 0.0) / width,
        (close - low) / width,
        112.0,
        88.0,
        118.0,
        82.0,
        100.0,
        0.5,
        2,
        2,
    )


def auction(end, open_, high, low, close, volume=100.0, flow=0.0):
    return _AuctionBar(
        end - 1,
        end,
        open_,
        high,
        low,
        close,
        volume,
        volume * (flow + 1.0) / 2.0,
        100,
    )


class HSCTests(unittest.TestCase):
    def params(self, **overrides):
        params = {
            "hsc_bias_period_minutes": 30,
            "hsc_liquidity_period_minutes": 5,
            "hsc_bias_atr_bars": 2,
            "hsc_bias_volume_bars": 2,
            "hsc_bias_breakout_lookback": 2,
            "hsc_bias_acceptance_close_atr": 0.02,
            "hsc_bias_range_atr": 0.75,
            "hsc_bias_body_fraction": 0.50,
            "hsc_bias_relative_volume": 0.95,
            "hsc_bias_flow_ratio": 0.04,
            "hsc_bias_close_location": 0.68,
            "hsc_bias_lifetime_periods": 3.0,
            "hsc_bias_boundary_loss_atr": 0.08,
            "hsc_sweep_min_atr_1m": 0.10,
            "hsc_sweep_opposing_flow_ratio": 0.03,
            "hsc_sweep_reclaim_tolerance_atr_1m": 0.02,
            "hsc_max_impulse_position": 0.70,
            "hsc_response_bars": 3,
            "hsc_response_body_atr_1m": 0.20,
            "hsc_response_flow_ratio": 0.05,
            "hsc_response_close_location": 0.62,
            "hsc_response_mode": "BREAK_SWEEP_BAR",
            "hsc_stop_buffer_atr_htf": 0.025,
            "hsc_extension_atr_htf": 0.50,
            "hsc_cooldown_bars": 2,
            "minimum_structural_rr": 0.75,
        }
        params.update(overrides)
        return params

    def seeded(self, **overrides):
        engine = HierarchicalLiquiditySweepContinuationEngine(self.params(**overrides))
        first = auction(1, 95.0, 101.0, 94.0, 100.0)
        second = auction(2, 99.0, 102.0, 98.0, 101.0)
        engine._bias_history = [first, second]
        engine._bias_true_ranges = [7.0, 4.0]
        engine._bias_volumes = [100.0, 100.0]
        return engine

    def start_long_bias(self, engine):
        bar = auction(3, 101.0, 108.0, 100.5, 107.5, 140.0, 0.40)
        transitions = engine._evaluate_completed_bias(
            bar,
            snap(10, 3, 101.0, 108.0, 100.5, 107.5, 0.40),
        )
        self.assertEqual(len(transitions), 1)
        self.assertIsNotNone(engine._bias)
        assert engine._bias is not None
        self.assertEqual(engine._bias.direction, "LONG")

    def start_short_bias(self, engine):
        bar = auction(3, 99.0, 99.5, 92.0, 92.5, 140.0, -0.40)
        transitions = engine._evaluate_completed_bias(
            bar,
            snap(10, 3, 99.0, 99.5, 92.0, 92.5, -0.40),
        )
        self.assertEqual(len(transitions), 1)
        self.assertIsNotNone(engine._bias)
        assert engine._bias is not None
        self.assertEqual(engine._bias.direction, "SHORT")

    @staticmethod
    def add_long_level(engine, low=103.0, high=105.0):
        engine._liquidity_history = [auction(20, 104.0, high, low, 104.5)]

    @staticmethod
    def add_short_level(engine, low=92.5, high=93.5):
        engine._liquidity_history = [auction(20, 93.0, high, low, 92.8)]

    def test_periods_validate(self):
        with self.assertRaises(ValueError):
            HierarchicalLiquiditySweepContinuationEngine(
                self.params(hsc_liquidity_period_minutes=30),
            )

    def test_completed_liquidity_bar_not_visible_early(self):
        engine = HierarchicalLiquiditySweepContinuationEngine(self.params())
        for index in range(4):
            engine.observe(
                snap(index, ns(0, index + 1), 100.0, 101.0, 99.0, 100.5),
                allow_new=True,
            )
            self.assertEqual(len(engine._liquidity_history), 0)
        engine.observe(
            snap(4, ns(0, 5), 100.5, 102.0, 100.0, 101.5),
            allow_new=True,
        )
        self.assertEqual(len(engine._liquidity_history), 1)

    def test_accepted_higher_timeframe_break_starts_bias(self):
        engine = self.seeded()
        self.start_long_bias(engine)
        assert engine._bias is not None
        self.assertEqual(engine._bias.boundary, 102.0)

    def test_opposing_sweep_requires_flow(self):
        engine = self.seeded()
        self.start_long_bias(engine)
        self.add_long_level(engine)
        transition = engine._maybe_start_sweep(
            snap(11, 11, 103.5, 104.0, 102.7, 103.2, 0.20),
        )
        self.assertIsNone(transition)

    def test_price_ablation_can_start_without_signed_flow(self):
        engine = self.seeded(hsc_use_flow_proxy=False)
        self.start_long_bias(engine)
        self.add_long_level(engine)
        transition = engine._maybe_start_sweep(
            snap(11, 11, 103.5, 104.0, 102.7, 103.2, 0.40),
        )
        self.assertIsNotNone(transition)

    def test_impulse_position_blocks_chasing(self):
        engine = self.seeded(hsc_max_impulse_position=0.30)
        self.start_long_bias(engine)
        self.add_long_level(engine, low=106.0, high=107.0)
        transition = engine._maybe_start_sweep(
            snap(11, 11, 106.5, 106.8, 105.7, 106.2, -0.30),
        )
        self.assertIsNone(transition)

    def test_long_sweep_then_separate_response_emits(self):
        engine = self.seeded()
        self.start_long_bias(engine)
        self.add_long_level(engine)
        transition = engine._maybe_start_sweep(
            snap(11, 11, 103.5, 104.0, 102.7, 103.2, -0.30),
        )
        self.assertIsNotNone(transition)
        self.assertIsNotNone(engine._sweep)
        no_signal = engine._advance_sweep(
            snap(12, 12, 103.2, 103.8, 102.8, 103.0, -0.10),
            allow_new=True,
        )
        self.assertIsNone(no_signal.signal)
        response = engine._advance_sweep(
            snap(13, 13, 103.0, 104.6, 102.9, 104.5, 0.30),
            allow_new=True,
        )
        self.assertIsNotNone(response.signal)
        assert response.signal is not None
        self.assertEqual(response.signal.direction, "LONG")
        self.assertLess(response.signal.stop_price, 102.7)

    def test_short_path_is_symmetric(self):
        engine = self.seeded()
        self.start_short_bias(engine)
        self.add_short_level(engine)
        transition = engine._maybe_start_sweep(
            snap(11, 11, 93.2, 93.8, 92.8, 93.4, 0.30),
        )
        self.assertIsNotNone(transition)
        engine._advance_sweep(
            snap(12, 12, 93.4, 93.7, 93.0, 93.6, 0.10),
            allow_new=True,
        )
        response = engine._advance_sweep(
            snap(13, 13, 93.6, 93.7, 92.0, 92.1, -0.30),
            allow_new=True,
        )
        self.assertIsNotNone(response.signal)
        assert response.signal is not None
        self.assertEqual(response.signal.direction, "SHORT")
        self.assertGreater(response.signal.stop_price, 93.8)

    def test_entry_slot_unavailable_resets_only_sweep(self):
        engine = self.seeded()
        self.start_long_bias(engine)
        self.add_long_level(engine)
        engine._maybe_start_sweep(
            snap(11, 11, 103.5, 104.0, 102.7, 103.2, -0.30),
        )
        response = engine._advance_sweep(
            snap(12, 12, 103.0, 104.6, 102.9, 104.5, 0.30),
            allow_new=False,
        )
        self.assertIsNone(response.signal)
        self.assertIsNone(engine._sweep)
        self.assertIsNotNone(engine._bias)
        self.assertEqual(
            response.transitions[-1].reason_code,
            "ENTRY_SLOT_UNAVAILABLE_AT_LTF_RESPONSE",
        )

    def test_bias_loss_resets_bias_and_sweep(self):
        engine = self.seeded()
        self.start_long_bias(engine)
        self.add_long_level(engine)
        engine._maybe_start_sweep(
            snap(11, 11, 103.5, 104.0, 102.7, 103.2, -0.30),
        )
        step = engine._advance_bias(
            snap(12, 12, 102.0, 102.2, 100.0, 101.0, -0.40),
        )
        self.assertIsNone(engine._bias)
        self.assertIsNone(engine._sweep)
        self.assertEqual(len(step.transitions), 2)

    def test_consumed_level_does_not_repeat_after_signal(self):
        engine = self.seeded()
        self.start_long_bias(engine)
        self.add_long_level(engine)
        engine._maybe_start_sweep(
            snap(11, 11, 103.5, 104.0, 102.7, 103.2, -0.30),
        )
        engine._advance_sweep(
            snap(12, 12, 103.0, 104.6, 102.9, 104.5, 0.30),
            allow_new=True,
        )
        self.assertIsNone(
            engine._maybe_start_sweep(
                snap(15, 15, 103.5, 104.0, 102.7, 103.2, -0.30),
            ),
        )

    def test_break_last_bar_is_weaker_but_separate(self):
        engine = self.seeded(hsc_response_mode="BREAK_LAST_BAR")
        self.start_long_bias(engine)
        self.add_long_level(engine)
        engine._maybe_start_sweep(
            snap(11, 11, 103.5, 104.5, 102.7, 103.2, -0.30),
        )
        engine._advance_sweep(
            snap(12, 12, 103.2, 103.8, 102.8, 103.0, -0.10),
            allow_new=True,
        )
        response = engine._advance_sweep(
            snap(13, 13, 103.0, 104.0, 102.9, 103.9, 0.30),
            allow_new=True,
        )
        self.assertIsNotNone(response.signal)


if __name__ == "__main__":
    unittest.main()
