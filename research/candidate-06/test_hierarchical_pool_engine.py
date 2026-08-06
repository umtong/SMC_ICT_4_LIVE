from __future__ import annotations

import unittest

from hierarchical_pool_engine import HierarchicalConfirmedPoolContinuationEngine, _LiquidityPool
from hierarchical_sweep_engine import _AuctionBar
from lrb_types import BarObservation, PrimitiveSnapshot


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


class PoolEngineTests(unittest.TestCase):
    def params(self, **overrides):
        params = {
            "hsc_bias_period_minutes": 60,
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
            "hsc_bias_lifetime_periods": 1.0,
            "hsp_bias_expiry_mode": "STRUCTURAL_ONLY",
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
            "hsc_use_flow_proxy": True,
            "hsp_liquidity_pool_mode": "CONFIRMED_SWING",
        }
        params.update(overrides)
        return params

    def seeded(self, **overrides):
        engine = HierarchicalConfirmedPoolContinuationEngine(self.params(**overrides))
        first = auction(1, 95.0, 101.0, 94.0, 100.0)
        second = auction(2, 99.0, 102.0, 98.0, 101.0)
        engine._bias_history = [first, second]
        engine._bias_true_ranges = [7.0, 4.0]
        engine._bias_volumes = [100.0, 100.0]
        return engine

    def start_long(self, engine):
        engine._evaluate_completed_bias(
            auction(3, 101.0, 108.0, 100.5, 107.5, 140.0, 0.40),
            snap(10, 3, 101.0, 108.0, 100.5, 107.5, 0.40),
        )
        self.assertIsNotNone(engine._bias)

    def start_short(self, engine):
        engine._evaluate_completed_bias(
            auction(3, 99.0, 99.5, 92.0, 92.5, 140.0, -0.40),
            snap(10, 3, 99.0, 99.5, 92.0, 92.5, -0.40),
        )
        self.assertIsNotNone(engine._bias)

    def test_pool_is_invisible_until_right_bar_completes(self):
        engine = self.seeded()
        engine._liquidity_history = [
            auction(1, 100.0, 102.0, 99.0, 101.0),
            auction(2, 101.0, 103.0, 97.0, 102.0),
        ]
        engine._confirm_liquidity_pools()
        self.assertEqual(engine._liquidity_pools, [])
        engine._liquidity_history.append(auction(3, 102.0, 104.0, 98.0, 103.0))
        engine._confirm_liquidity_pools()
        self.assertEqual(len(engine._liquidity_pools), 1)
        pool = engine._liquidity_pools[0]
        self.assertEqual((pool.side, pool.source_ts_ns, pool.confirmed_ts_ns), ("LOWER", 2, 3))

    def test_long_sweep_response_uses_nearest_opposite_pool(self):
        engine = self.seeded()
        self.start_long(engine)
        engine._liquidity_pools = [
            _LiquidityPool("LOWER", 103.0, 20, 25),
            _LiquidityPool("UPPER", 106.5, 21, 26),
        ]
        transition = engine._maybe_start_sweep(
            snap(11, 11, 103.5, 104.0, 102.7, 103.2, -0.30),
        )
        self.assertIsNotNone(transition)
        engine._advance_sweep(
            snap(12, 12, 103.2, 103.8, 102.8, 103.0, -0.10),
            allow_new=True,
        )
        step = engine._advance_sweep(
            snap(13, 13, 103.0, 104.6, 102.9, 104.5, 0.30),
            allow_new=True,
        )
        self.assertIsNotNone(step.signal)
        assert step.signal is not None
        self.assertEqual(step.signal.family, "HSP")
        self.assertEqual(step.signal.target_reason, "CONFIRMED_LTF_BUYSIDE_LIQUIDITY")
        self.assertEqual(step.signal.target_price, 106.5)

    def test_short_path_is_symmetric(self):
        engine = self.seeded()
        self.start_short(engine)
        engine._liquidity_pools = [
            _LiquidityPool("UPPER", 93.5, 20, 25),
            _LiquidityPool("LOWER", 90.0, 21, 26),
        ]
        self.assertIsNotNone(
            engine._maybe_start_sweep(
                snap(11, 11, 93.2, 93.8, 92.8, 93.4, 0.30),
            ),
        )
        engine._advance_sweep(
            snap(12, 12, 93.4, 93.7, 93.0, 93.6, 0.10),
            allow_new=True,
        )
        step = engine._advance_sweep(
            snap(13, 13, 93.6, 93.7, 92.0, 92.1, -0.30),
            allow_new=True,
        )
        self.assertIsNotNone(step.signal)
        assert step.signal is not None
        self.assertEqual(step.signal.direction, "SHORT")
        self.assertEqual(step.signal.target_reason, "CONFIRMED_LTF_SELLSIDE_LIQUIDITY")

    def test_structural_bias_does_not_expire_by_clock(self):
        engine = self.seeded()
        self.start_long(engine)
        assert engine._bias is not None
        engine._bias.expires_index = 11
        step = engine._advance_bias(
            snap(100, 100, 106.0, 107.0, 105.0, 106.5, 0.10),
        )
        self.assertFalse(step.transitions)
        self.assertIsNotNone(engine._bias)

    def test_boundary_loss_still_invalidates_structural_bias(self):
        engine = self.seeded()
        self.start_long(engine)
        step = engine._advance_bias(
            snap(12, 12, 102.0, 102.2, 100.0, 101.0, -0.40),
        )
        self.assertTrue(step.transitions)
        self.assertIsNone(engine._bias)

    def test_flow_ablation_changes_only_flow_gate(self):
        engine = self.seeded(hsc_use_flow_proxy=False)
        self.start_long(engine)
        engine._liquidity_pools = [_LiquidityPool("LOWER", 103.0, 20, 25)]
        self.assertIsNotNone(
            engine._maybe_start_sweep(
                snap(11, 11, 103.5, 104.0, 102.7, 103.2, 0.40),
            ),
        )


if __name__ == "__main__":
    unittest.main()
