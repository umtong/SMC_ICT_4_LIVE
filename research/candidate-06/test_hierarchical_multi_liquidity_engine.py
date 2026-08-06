from __future__ import annotations

import unittest

from hierarchical_multi_liquidity_engine import HierarchicalMultiLiquidityEngine
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


class MultiLiquidityTests(unittest.TestCase):
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
            "hff_use_sweep_flow": False,
            "hff_use_response_flow": True,
            "hml_pool_families": "SWING_AND_EQUAL",
            "hml_equal_lookback_bars": 8,
            "hml_equal_min_intervening_bars": 1,
            "hml_equal_tolerance_range_fraction": 0.08,
            "hml_equal_rejection_close_fraction": 0.35,
        }
        params.update(overrides)
        return params

    def seeded(self, **overrides):
        engine = HierarchicalMultiLiquidityEngine(self.params(**overrides))
        first = auction(1, 95.0, 101.0, 94.0, 100.0)
        second = auction(2, 99.0, 102.0, 98.0, 101.0)
        engine._bias_history = [first, second]
        engine._bias_true_ranges = [7.0, 4.0]
        engine._bias_volumes = [100.0, 100.0]
        return engine

    def start_long(self, engine):
        transitions = engine._evaluate_completed_bias(
            auction(3, 101.0, 108.0, 100.5, 107.5, 140.0, 0.40),
            snap(10, 3, 101.0, 108.0, 100.5, 107.5, 0.40),
        )
        self.assertTrue(transitions)
        self.assertIsNotNone(engine._bias)

    def test_equal_low_requires_second_completed_touch_and_separation(self):
        engine = self.seeded(hml_pool_families="EQUAL_ONLY")
        first = auction(10, 102.0, 104.0, 100.0, 103.0)
        middle = auction(11, 103.0, 105.0, 101.5, 104.0)
        second = auction(12, 104.0, 105.5, 100.1, 104.5)
        engine._liquidity_history = [first, middle]
        engine._confirm_liquidity_pools()
        self.assertEqual(engine._liquidity_pools, [])
        engine._liquidity_history.append(second)
        engine._confirm_liquidity_pools()
        self.assertEqual(len(engine._liquidity_pools), 1)
        pool = engine._liquidity_pools[0]
        self.assertEqual(pool.side, "LOWER")
        self.assertEqual(engine._pool_kinds[(pool.side, pool.source_ts_ns)], "EQUAL_LOW")

    def test_equal_high_is_symmetric(self):
        engine = self.seeded(hml_pool_families="EQUAL_ONLY")
        engine._liquidity_history = [
            auction(10, 100.0, 105.0, 99.0, 101.0),
            auction(11, 101.0, 103.0, 98.0, 100.0),
            auction(12, 100.0, 105.1, 99.0, 100.5),
        ]
        engine._confirm_liquidity_pools()
        self.assertEqual(len(engine._liquidity_pools), 1)
        pool = engine._liquidity_pools[0]
        self.assertEqual(pool.side, "UPPER")
        self.assertEqual(engine._pool_kinds[(pool.side, pool.source_ts_ns)], "EQUAL_HIGH")

    def test_equal_pool_is_not_duplicated_while_unconsumed(self):
        engine = self.seeded(hml_pool_families="EQUAL_ONLY")
        engine._liquidity_history = [
            auction(10, 102.0, 104.0, 100.0, 103.0),
            auction(11, 103.0, 105.0, 101.5, 104.0),
            auction(12, 104.0, 105.5, 100.1, 104.5),
        ]
        engine._confirm_liquidity_pools()
        engine._liquidity_history.extend(
            [
                auction(13, 104.0, 106.0, 102.0, 105.0),
                auction(14, 105.0, 106.0, 100.05, 105.5),
            ],
        )
        engine._confirm_liquidity_pools()
        self.assertEqual(len([pool for pool in engine._liquidity_pools if pool.side == "LOWER"]), 1)

    def test_swing_and_equal_contract_keeps_both_pool_types(self):
        engine = self.seeded(hml_pool_families="SWING_AND_EQUAL")
        bars = [
            auction(10, 102.0, 104.0, 100.0, 103.0),
            auction(11, 103.0, 105.0, 98.0, 104.0),
            auction(12, 104.0, 106.0, 100.0, 105.0),
            auction(13, 105.0, 106.5, 101.0, 105.5),
            auction(14, 105.5, 107.0, 100.1, 106.0),
        ]
        engine._liquidity_history = bars[:3]
        engine._confirm_liquidity_pools()
        self.assertIn("CONFIRMED_SWING", set(engine._pool_kinds.values()))
        engine._liquidity_history.append(bars[3])
        engine._confirm_liquidity_pools()
        engine._liquidity_history.append(bars[4])
        engine._confirm_liquidity_pools()
        kinds = set(engine._pool_kinds.values())
        self.assertIn("CONFIRMED_SWING", kinds)
        self.assertTrue(any(kind.startswith("EQUAL_") for kind in kinds))

    def test_equal_lower_sweep_and_response_emit_hml(self):
        engine = self.seeded(hml_pool_families="SWING_AND_EQUAL")
        self.start_long(engine)
        lower = _LiquidityPool("LOWER", 103.0, 20, 25)
        upper = _LiquidityPool("UPPER", 106.5, 21, 26)
        engine._liquidity_pools = [lower, upper]
        engine._pool_kinds = {
            ("LOWER", 20): "EQUAL_LOW",
            ("UPPER", 21): "EQUAL_HIGH",
        }
        engine._pool_touches = {("LOWER", 20): 2, ("UPPER", 21): 2}
        transition = engine._maybe_start_sweep(
            snap(11, 11, 103.5, 104.0, 102.7, 103.2, 0.20),
        )
        self.assertIsNotNone(transition)
        assert transition is not None
        self.assertEqual(
            transition.reason_code,
            "CONFIRMED_EQUAL_LTF_LIQUIDITY_SWEPT_AGAINST_ACCEPTED_BIAS",
        )
        response = engine._advance_sweep(
            snap(12, 12, 103.0, 104.6, 102.9, 104.5, 0.30),
            allow_new=True,
        )
        self.assertIsNotNone(response.signal)
        assert response.signal is not None
        self.assertEqual(response.signal.family, "HML")
        self.assertEqual(response.signal.target_reason, "EQUAL_LTF_BUYSIDE_LIQUIDITY")
        self.assertEqual(
            response.signal.details["flow_stage_contract"],
            {"bias": True, "sweep": False, "response": True},
        )
        self.assertEqual(response.signal.details["swept_pool_kind"], "EQUAL_LOW")

    def test_response_flow_remains_required(self):
        engine = self.seeded()
        self.start_long(engine)
        lower = _LiquidityPool("LOWER", 103.0, 20, 25)
        upper = _LiquidityPool("UPPER", 106.5, 21, 26)
        engine._liquidity_pools = [lower, upper]
        engine._pool_kinds = {("LOWER", 20): "EQUAL_LOW", ("UPPER", 21): "EQUAL_HIGH"}
        engine._pool_touches = {("LOWER", 20): 2, ("UPPER", 21): 2}
        engine._maybe_start_sweep(snap(11, 11, 103.5, 104.0, 102.7, 103.2, 0.20))
        blocked = engine._advance_sweep(
            snap(12, 12, 103.0, 104.6, 102.9, 104.5, -0.30),
            allow_new=True,
        )
        self.assertIsNone(blocked.signal)


if __name__ == "__main__":
    unittest.main()
