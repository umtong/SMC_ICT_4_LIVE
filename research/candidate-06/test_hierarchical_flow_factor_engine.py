from __future__ import annotations

import unittest

from hierarchical_flow_factor_engine import HierarchicalFlowFactorizedEngine
from hierarchical_pool_engine import _LiquidityPool
from hierarchical_sweep_engine import _AuctionBar
from lrb_types import BarObservation, PrimitiveSnapshot


def snap(index, timestamp, open_, high, low, close, flow=0.0, volume=100.0):
    width = max(high - low, 0.1)
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(
            timestamp,
            open_,
            high,
            low,
            close,
            volume,
            volume * (flow + 1.0) / 2.0,
            10,
        ),
        ready=True,
        atr=1.0,
        rel_volume=1.5,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=max(high - max(open_, close), 0.0) / width,
        lower_wick_fraction=max(min(open_, close) - low, 0.0) / width,
        close_location=(close - low) / width,
        upper_fast=112.0,
        lower_fast=88.0,
        upper_slow=118.0,
        lower_slow=82.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
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


class FlowFactorTests(unittest.TestCase):
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
            "hsc_bias_lifetime_periods": 3.0,
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
            "hsc_response_mode": "BREAK_LAST_BAR",
            "hsc_stop_buffer_atr_htf": 0.025,
            "hsc_extension_atr_htf": 0.50,
            "hsc_cooldown_bars": 2,
            "minimum_structural_rr": 0.75,
            "hsp_liquidity_pool_mode": "CONFIRMED_SWING",
            "hsc_use_flow_proxy": True,
            "hff_use_bias_flow": True,
            "hff_use_sweep_flow": True,
            "hff_use_response_flow": True,
        }
        params.update(overrides)
        return params

    def seeded(self, **overrides):
        engine = HierarchicalFlowFactorizedEngine(self.params(**overrides))
        first = auction(1, 95.0, 101.0, 94.0, 100.0)
        second = auction(2, 99.0, 102.0, 98.0, 101.0)
        engine._bias_history = [first, second]
        engine._bias_true_ranges = [7.0, 4.0]
        engine._bias_volumes = [100.0, 100.0]
        return engine

    def start_long(self, engine, flow=0.40):
        transitions = engine._evaluate_completed_bias(
            auction(3, 101.0, 108.0, 100.5, 107.5, 140.0, flow),
            snap(10, 3, 101.0, 108.0, 100.5, 107.5, flow),
        )
        return transitions

    def add_pools(self, engine):
        engine._liquidity_pools = [
            _LiquidityPool("LOWER", 103.0, 20, 25),
            _LiquidityPool("UPPER", 106.5, 21, 26),
        ]

    def test_bias_stage_flow_is_independent(self):
        strict = self.seeded(hff_use_bias_flow=True)
        self.assertFalse(self.start_long(strict, flow=-0.40))
        relaxed = self.seeded(hff_use_bias_flow=False)
        self.assertTrue(self.start_long(relaxed, flow=-0.40))
        self.assertIsNotNone(relaxed._bias)

    def test_sweep_stage_flow_is_independent(self):
        strict = self.seeded(hff_use_sweep_flow=True)
        self.start_long(strict)
        self.add_pools(strict)
        self.assertIsNone(
            strict._maybe_start_sweep(
                snap(11, 11, 103.5, 104.0, 102.7, 103.2, 0.30),
            ),
        )
        relaxed = self.seeded(hff_use_sweep_flow=False)
        self.start_long(relaxed)
        self.add_pools(relaxed)
        self.assertIsNotNone(
            relaxed._maybe_start_sweep(
                snap(11, 11, 103.5, 104.0, 102.7, 103.2, 0.30),
            ),
        )

    def test_response_stage_flow_is_independent(self):
        strict = self.seeded(hff_use_response_flow=True)
        self.start_long(strict)
        self.add_pools(strict)
        strict._maybe_start_sweep(
            snap(11, 11, 103.5, 104.0, 102.7, 103.2, -0.30),
        )
        blocked = strict._advance_sweep(
            snap(12, 12, 103.0, 104.6, 102.9, 104.5, -0.30),
            allow_new=True,
        )
        self.assertIsNone(blocked.signal)

        relaxed = self.seeded(hff_use_response_flow=False)
        self.start_long(relaxed)
        self.add_pools(relaxed)
        relaxed._maybe_start_sweep(
            snap(11, 11, 103.5, 104.0, 102.7, 103.2, -0.30),
        )
        emitted = relaxed._advance_sweep(
            snap(12, 12, 103.0, 104.6, 102.9, 104.5, -0.30),
            allow_new=True,
        )
        self.assertIsNotNone(emitted.signal)
        assert emitted.signal is not None
        self.assertEqual(emitted.signal.family, "HFF")
        self.assertEqual(
            emitted.signal.details["flow_stage_contract"],
            {"bias": True, "sweep": True, "response": False},
        )

    def test_legacy_flag_is_restored_after_scoped_call(self):
        engine = self.seeded(hsc_use_flow_proxy=False, hff_use_bias_flow=True)
        self.start_long(engine, flow=-0.40)
        self.assertFalse(engine.params["hsc_use_flow_proxy"])

    def test_default_stage_flags_follow_legacy_switch(self):
        engine = self.seeded(
            hsc_use_flow_proxy=False,
            hff_use_bias_flow=False,
            hff_use_sweep_flow=False,
            hff_use_response_flow=False,
        )
        self.assertFalse(engine._stage_flag("hff_use_bias_flow"))
        self.assertFalse(engine._stage_flag("hff_use_sweep_flow"))
        self.assertFalse(engine._stage_flag("hff_use_response_flow"))


if __name__ == "__main__":
    unittest.main()
