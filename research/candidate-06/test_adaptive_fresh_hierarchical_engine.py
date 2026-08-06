from __future__ import annotations

import unittest

from adaptive_fresh_core import DirectionalFreshnessClock
from adaptive_fresh_hierarchical_engine import AdaptiveFreshHierarchicalEngine
from hierarchical_sweep_engine import _AuctionBar, _Bias, _SweepEpisode
from lrb_types import BarObservation, PrimitiveSnapshot


def snap(index: int, timestamp: int, open_: float, high: float, low: float, close: float, flow: float = 0.2) -> PrimitiveSnapshot:
    width = max(high - low, 0.1)
    volume = 100.0
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(timestamp, open_, high, low, close, volume, volume * (flow + 1.0) / 2.0, 10),
        ready=True,
        atr=1.0,
        rel_volume=1.0,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=max(high - max(open_, close), 0.0) / width,
        lower_wick_fraction=max(min(open_, close) - low, 0.0) / width,
        close_location=(close - low) / width,
        upper_fast=120.0,
        lower_fast=80.0,
        upper_slow=125.0,
        lower_slow=75.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


def auction(end: int, open_: float, high: float, low: float, close: float, volume: float, flow: float = 0.2) -> _AuctionBar:
    return _AuctionBar(end - 1, end, open_, high, low, close, volume, volume * (flow + 1.0) / 2.0, 100)


class AdaptiveFreshHierarchicalTests(unittest.TestCase):
    def params(self, **overrides):
        values = {
            "hsc_bias_period_minutes": 60,
            "hsc_liquidity_period_minutes": 5,
            "hsc_bias_atr_bars": 4,
            "hsc_bias_volume_bars": 4,
            "hsc_bias_breakout_lookback": 4,
            "hsc_bias_acceptance_close_atr": 0.0,
            "hsc_bias_range_atr": 0.75,
            "hsc_bias_body_fraction": 0.50,
            "hsc_bias_relative_volume": 0.50,
            "hsc_bias_flow_ratio": 0.04,
            "hsc_bias_close_location": 0.68,
            "hsp_bias_expiry_mode": "STRUCTURAL_ONLY",
            "hsc_bias_boundary_loss_atr": 0.08,
            "hsc_use_flow_proxy": True,
            "hff_use_bias_flow": True,
            "hff_use_sweep_flow": False,
            "hff_use_response_flow": True,
            "afhr_use_adaptive_quality": True,
            "afhr_quality_lookback": 4,
            "afhr_quality_min_history": 4,
            "afhr_quality_quantile": 0.75,
            "afhr_quality_body_fraction": 0.65,
            "afhr_use_extreme_freshness": True,
            "afhr_stale_periods": 0.05,
            "hsc_cooldown_bars": 2,
        }
        values.update(overrides)
        return values

    def seeded(self, **overrides) -> AdaptiveFreshHierarchicalEngine:
        engine = AdaptiveFreshHierarchicalEngine(self.params(**overrides))
        engine._bias_history = [
            auction(1, 90.0, 100.0, 90.0, 98.0, 200.0),
            auction(2, 91.0, 101.0, 91.0, 99.0, 200.0),
            auction(3, 92.0, 102.0, 92.0, 100.0, 200.0),
            auction(4, 93.0, 103.0, 93.0, 101.0, 200.0),
        ]
        engine._bias_true_ranges = [10.0] * 4
        engine._bias_volumes = [200.0] * 4
        return engine

    def test_baseline_break_can_be_rejected_as_nonexceptional(self) -> None:
        engine = self.seeded()
        weak = auction(5, 103.0, 111.0, 103.0, 109.0, 120.0, 0.2)
        transitions = engine._evaluate_completed_bias(weak, snap(10, 5, 103.0, 111.0, 103.0, 109.0))
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].reason_code, "HTF_ACCEPTANCE_NOT_EXCEPTIONAL_TO_PRIOR_DISTRIBUTION")
        self.assertIsNone(engine._bias)

    def test_exceptional_break_creates_bias_and_seals_quality_evidence(self) -> None:
        engine = self.seeded()
        strong = auction(5, 103.0, 115.0, 103.0, 113.0, 260.0, 0.2)
        transitions = engine._evaluate_completed_bias(strong, snap(10, 5, 103.0, 115.0, 103.0, 113.0))
        self.assertTrue(transitions)
        self.assertIsNotNone(engine._bias)
        assert engine._bias is not None
        quality = engine._quality_by_context[engine._bias.context_id]
        self.assertTrue(quality["passed"])

    def test_quality_disabled_is_parent_only_ablation(self) -> None:
        engine = self.seeded(afhr_use_adaptive_quality=False)
        weak = auction(5, 103.0, 111.0, 103.0, 109.0, 120.0, 0.2)
        transitions = engine._evaluate_completed_bias(weak, snap(10, 5, 103.0, 111.0, 103.0, 109.0))
        self.assertTrue(transitions)
        self.assertIsNotNone(engine._bias)

    def test_stale_bias_and_active_sweep_reset_together(self) -> None:
        engine = self.seeded()
        bias = _Bias("TEST-BIAS", "LONG", 95.0, 98.0, 101.0, 97.0, 100.0, 101.0, 10.0, 0, 10_000, 1.0, 0.8, 0.2, 2.0)
        engine._bias = bias
        engine._sweep = _SweepEpisode("TEST-SWEEP", "LONG", "COUNTER_DIRECTION_LIQUIDITY_SWEEP", 98.0, 1, 0, 1, 97.5, 100.5, 100.5, 97.5, 0.4, -0.2)
        engine._freshness_by_context[bias.context_id] = DirectionalFreshnessClock("LONG", 100.0, 0)
        final = None
        for index in range(1, 5):
            final = engine._advance_bias(snap(index, index, 99.0, 100.5, 98.0, 99.5))
        assert final is not None
        self.assertIsNone(engine._bias)
        self.assertIsNone(engine._sweep)
        self.assertTrue(any(t.reason_code == "HTF_ACCEPTANCE_EXTREME_NOT_REFRESHED" for t in final.transitions))

    def test_new_completed_close_extreme_refreshes_context(self) -> None:
        engine = self.seeded()
        bias = _Bias("TEST-BIAS", "LONG", 95.0, 98.0, 101.0, 97.0, 100.0, 101.0, 10.0, 0, 10_000, 1.0, 0.8, 0.2, 2.0)
        engine._bias = bias
        engine._freshness_by_context[bias.context_id] = DirectionalFreshnessClock("LONG", 100.0, 0)
        engine._advance_bias(snap(1, 1, 99.0, 100.0, 98.0, 99.5))
        engine._advance_bias(snap(2, 2, 99.5, 101.5, 99.0, 101.0))
        engine._advance_bias(snap(4, 4, 100.0, 101.2, 99.0, 100.5))
        self.assertIsNotNone(engine._bias)
        clock = engine._freshness_by_context[bias.context_id]
        self.assertEqual(clock.last_refresh_index, 2)
        self.assertEqual(clock.age(4), 2)


if __name__ == "__main__":
    unittest.main()
