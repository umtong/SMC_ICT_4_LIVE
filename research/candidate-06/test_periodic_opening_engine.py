from __future__ import annotations

from datetime import datetime, timezone
import unittest

from hierarchical_pool_engine import _LiquidityPool
from hierarchical_sweep_engine import _AuctionBar
from lrb_types import BarObservation, PrimitiveSnapshot
from periodic_opening_engine import PeriodicOpeningLiquidityRelayEngine


def ns(hour: int, minute: int) -> int:
    return int(datetime(2024, 2, 26, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def snap(index, timestamp, open_, high, low, close, flow=0.0, volume=100.0, atr=1.0):
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
        atr=atr,
        rel_volume=1.5,
        flow_ratio=flow,
        body_atr=abs(close - open_) / atr,
        range_atr=width / atr,
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


class PeriodicOpeningTests(unittest.TestCase):
    def params(self, **overrides):
        params = {
            "hsc_bias_period_minutes": 60,
            "hsc_liquidity_period_minutes": 5,
            "hsp_bias_expiry_mode": "FIXED_PERIODS",
            "hsc_bias_boundary_loss_atr": 0.10,
            "hsp_liquidity_pool_mode": "CONFIRMED_SWING",
            "hml_pool_families": "SWING_ONLY",
            "hsc_sweep_min_atr_1m": 0.10,
            "hsc_sweep_opposing_flow_ratio": 0.03,
            "hsc_sweep_reclaim_tolerance_atr_1m": 0.02,
            "hsc_max_impulse_position": 0.85,
            "hsc_response_bars": 3,
            "hsc_response_body_atr_1m": 0.20,
            "hsc_response_flow_ratio": 0.05,
            "hsc_response_close_location": 0.62,
            "hsc_response_mode": "BREAK_SWEEP_BAR",
            "hsc_stop_buffer_atr_htf": 0.025,
            "hsc_extension_atr_htf": 0.50,
            "hsc_cooldown_bars": 2,
            "hsc_use_flow_proxy": True,
            "hff_use_bias_flow": True,
            "hff_use_sweep_flow": False,
            "hff_use_response_flow": True,
            "minimum_structural_rr": 0.75,
            "poil_opening_history": 16,
            "poil_quarter_atr_bars": 8,
            "poil_opening_volume_multiple": 1.15,
            "poil_opening_pressure_multiple": 1.50,
            "poil_opening_flow_ratio": 0.12,
            "poil_opening_body_atr_1m": 0.25,
            "poil_opening_range_atr_1m": 0.50,
            "poil_opening_body_fraction": 0.45,
            "poil_opening_close_location": 0.65,
            "poil_bias_horizon_minutes": 240,
        }
        params.update(overrides)
        return params

    def seeded(self, **overrides):
        engine = PeriodicOpeningLiquidityRelayEngine(self.params(**overrides))
        engine._opening_volumes = [100.0] * 16
        engine._opening_abs_signed = [10.0] * 16
        engine._opening_ranges = [1.0] * 16
        engine._quarter_true_ranges = [20.0] * 8
        engine._quarter_history = [auction(i, 100.0, 110.0, 90.0, 100.0) for i in range(8)]
        return engine

    def strong_long(self, index=100, timestamp=None):
        return snap(
            index,
            ns(4, 1) if timestamp is None else timestamp,
            100.0,
            103.0,
            99.8,
            102.8,
            0.30,
            200.0,
        )

    def strong_short(self, index=101, timestamp=None):
        return snap(
            index,
            ns(4, 16) if timestamp is None else timestamp,
            103.0,
            103.2,
            100.0,
            100.2,
            -0.30,
            200.0,
        )

    def test_non_opening_minute_does_not_create_bias(self):
        engine = self.seeded()
        step = engine.observe(
            self.strong_long(timestamp=ns(4, 2)),
            allow_new=True,
        )
        self.assertIsNone(engine._bias)
        self.assertFalse(any(e.reason_code == "QUARTER_HOUR_OPENING_IMBALANCE_ACCEPTED" for e in step.transitions))

    def test_opening_uses_prior_history_and_creates_long_bias(self):
        engine = self.seeded()
        step = engine.observe(self.strong_long(), allow_new=True)
        self.assertIsNotNone(engine._bias)
        assert engine._bias is not None
        self.assertEqual(engine._bias.direction, "LONG")
        self.assertEqual(engine._bias.boundary, 99.8)
        event = next(e for e in step.transitions if e.reason_code == "QUARTER_HOUR_OPENING_IMBALANCE_ACCEPTED")
        self.assertGreater(event.details["opening_pressure_multiple"], 1.5)
        self.assertEqual(len(engine._opening_volumes), 17)

    def test_current_opening_only_completes_warmup_after_decision(self):
        engine = self.seeded()
        engine._opening_volumes = engine._opening_volumes[:15]
        engine._opening_abs_signed = engine._opening_abs_signed[:15]
        engine._opening_ranges = engine._opening_ranges[:15]
        engine.observe(self.strong_long(), allow_new=True)
        self.assertIsNone(engine._bias)
        self.assertEqual(len(engine._opening_volumes), 16)
        engine.observe(
            self.strong_long(index=115, timestamp=ns(4, 16)),
            allow_new=True,
        )
        self.assertIsNotNone(engine._bias)

    def test_opposite_opening_replaces_bias_and_aborts_sweep(self):
        engine = self.seeded()
        engine.observe(self.strong_long(), allow_new=True)
        engine._liquidity_pools = [_LiquidityPool("LOWER", 101.5, 20, 25)]
        engine._pool_kinds = {("LOWER", 20): "CONFIRMED_SWING"}
        engine._pool_touches = {("LOWER", 20): 1}
        engine._maybe_start_sweep(snap(101, ns(4, 5), 102.0, 102.2, 101.3, 101.6, 0.10))
        self.assertIsNotNone(engine._sweep)
        step = engine.observe(self.strong_short(index=115), allow_new=True)
        self.assertIsNotNone(engine._bias)
        assert engine._bias is not None
        self.assertEqual(engine._bias.direction, "SHORT")
        self.assertIsNone(engine._sweep)
        reasons = [event.reason_code for event in step.transitions]
        self.assertIn("PERIODIC_OPENING_BIAS_REFRESHED", reasons)
        self.assertIn("PERIODIC_OPENING_BIAS_REPLACED", reasons)

    def test_opening_bar_cannot_consume_pool_created_before_it(self):
        engine = self.seeded()
        engine._liquidity_pools = [_LiquidityPool("LOWER", 101.0, 20, 25)]
        engine._pool_kinds = {("LOWER", 20): "CONFIRMED_SWING"}
        engine._pool_touches = {("LOWER", 20): 1}
        engine.observe(self.strong_long(), allow_new=True)
        self.assertIsNotNone(engine._bias)
        self.assertIsNone(engine._sweep)

    def test_fixed_horizon_expires_periodic_bias(self):
        engine = self.seeded(poil_bias_horizon_minutes=4)
        engine.observe(self.strong_long(index=100), allow_new=True)
        self.assertIsNotNone(engine._bias)
        step = engine._advance_bias(
            snap(105, ns(4, 6), 102.0, 103.0, 101.5, 102.5, 0.10),
        )
        self.assertIsNone(engine._bias)
        self.assertEqual(step.transitions[-1].reason_code, "HIGHER_TIMEFRAME_BIAS_EXPIRED")

    def test_structural_hourly_breakout_is_not_a_bias_source(self):
        engine = self.seeded()
        transitions = engine._evaluate_completed_bias(
            auction(10, 100.0, 110.0, 99.0, 109.0, 300.0, 0.40),
            self.strong_long(),
        )
        self.assertEqual(transitions, ())
        self.assertIsNone(engine._bias)

    def test_periodic_bias_pool_sweep_and_response_emit_poil(self):
        engine = self.seeded()
        engine.observe(self.strong_long(), allow_new=True)
        lower = _LiquidityPool("LOWER", 101.5, 20, 25)
        upper = _LiquidityPool("UPPER", 104.5, 21, 26)
        engine._liquidity_pools = [lower, upper]
        engine._pool_kinds = {("LOWER", 20): "CONFIRMED_SWING", ("UPPER", 21): "CONFIRMED_SWING"}
        engine._pool_touches = {("LOWER", 20): 1, ("UPPER", 21): 1}
        transition = engine._maybe_start_sweep(
            snap(101, ns(4, 5), 102.0, 102.3, 101.3, 101.7, 0.20),
        )
        self.assertIsNotNone(transition)
        response = engine._advance_sweep(
            snap(102, ns(4, 6), 101.7, 103.0, 101.6, 102.9, 0.30),
            allow_new=True,
        )
        self.assertIsNotNone(response.signal)
        assert response.signal is not None
        self.assertEqual(response.signal.family, "POIL")
        self.assertEqual(response.signal.direction, "LONG")
        self.assertTrue(response.signal.details["periodic_context"])

    def test_response_flow_remains_required(self):
        engine = self.seeded()
        engine.observe(self.strong_long(), allow_new=True)
        engine._liquidity_pools = [
            _LiquidityPool("LOWER", 101.5, 20, 25),
            _LiquidityPool("UPPER", 104.5, 21, 26),
        ]
        engine._pool_kinds = {("LOWER", 20): "CONFIRMED_SWING", ("UPPER", 21): "CONFIRMED_SWING"}
        engine._pool_touches = {("LOWER", 20): 1, ("UPPER", 21): 1}
        engine._maybe_start_sweep(
            snap(101, ns(4, 5), 102.0, 102.3, 101.3, 101.7, 0.20),
        )
        blocked = engine._advance_sweep(
            snap(102, ns(4, 6), 101.7, 103.0, 101.6, 102.9, -0.30),
            allow_new=True,
        )
        self.assertIsNone(blocked.signal)


if __name__ == "__main__":
    unittest.main()
