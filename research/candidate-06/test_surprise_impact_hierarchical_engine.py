from __future__ import annotations

import unittest

from hierarchical_sweep_engine import _AuctionBar
from lrb_types import BarObservation, PrimitiveSnapshot
from surprise_impact_hierarchical_engine import SurpriseImpactHierarchicalEngine


def auction(end: int, open_: float, high: float, low: float, close: float, volume: float, flow: float) -> _AuctionBar:
    return _AuctionBar(end - 1, end, open_, high, low, close, volume, volume * (flow + 1.0) / 2.0, 100)


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


class SurpriseImpactEngineTests(unittest.TestCase):
    def params(self, **overrides):
        values = {
            "hsc_bias_period_minutes": 60,
            "hsc_liquidity_period_minutes": 5,
            "hsc_bias_atr_bars": 8,
            "hsc_bias_volume_bars": 8,
            "hsc_bias_breakout_lookback": 4,
            "hsc_bias_acceptance_close_atr": 0.0,
            "hsc_bias_range_atr": 0.50,
            "hsc_bias_body_fraction": 0.40,
            "hsc_bias_relative_volume": 0.50,
            "hsc_bias_flow_ratio": 0.01,
            "hsc_bias_close_location": 0.60,
            "hsp_bias_expiry_mode": "STRUCTURAL_ONLY",
            "hsc_bias_boundary_loss_atr": 0.08,
            "hsc_use_flow_proxy": True,
            "hff_use_bias_flow": True,
            "hff_use_sweep_flow": False,
            "hff_use_response_flow": True,
            "afhr_use_extreme_freshness": True,
            "afhr_stale_periods": 6.0,
            "siar_use_flow_surprise": True,
            "siar_use_impact_efficiency": True,
            "siar_flow_lookback": 8,
            "siar_min_history": 8,
            "siar_surprise_quantile": 0.75,
            "siar_min_efficiency_history": 4,
        }
        values.update(overrides)
        return values

    def seeded(self, **overrides):
        engine = SurpriseImpactHierarchicalEngine(self.params(**overrides))
        flows = [-0.4, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, -0.3]
        closes = [98.0, 99.0, 100.0, 100.0, 101.0, 102.5, 103.5, 97.5]
        engine._bias_history = [
            auction(index + 1, 100.0, max(104.0, close + 1.0), min(96.0, close - 1.0), close, 200.0, flow)
            for index, (flow, close) in enumerate(zip(flows, closes))
        ]
        engine._bias_true_ranges = [10.0] * 8
        engine._bias_volumes = [200.0] * 8
        return engine

    def test_weak_response_rejects_baseline_break_as_absorption(self) -> None:
        engine = self.seeded()
        bar = auction(9, 102.5, 105.0, 100.0, 104.8, 300.0, 0.8)
        transitions = engine._evaluate_completed_bias(bar, snap(10, 9, 102.5, 105.0, 100.0, 104.8, 0.8))
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].previous_state, "IDLE")
        self.assertEqual(transitions[0].next_state, "RESET")
        self.assertIn(transitions[0].reason_code, {
            "FLOW_SURPRISE_ABSORBED_WITH_WEAK_PRICE_RESPONSE",
            "DIRECTIONAL_FLOW_NOT_SURPRISING_TO_PRIOR_EXPECTATION",
        })
        self.assertIsNone(engine._bias)

    def test_strong_surprise_and_response_create_bias(self) -> None:
        engine = self.seeded()
        bar = auction(9, 104.0, 122.0, 104.0, 120.0, 320.0, 0.9)
        transitions = engine._evaluate_completed_bias(bar, snap(10, 9, 104.0, 122.0, 104.0, 120.0, 0.9))
        self.assertTrue(transitions)
        self.assertIsNotNone(engine._bias)
        assert engine._bias is not None
        contract = engine._siar_by_context[engine._bias.context_id]
        self.assertTrue(contract["passed"])

    def test_impact_efficiency_can_be_removed_without_changing_surprise(self) -> None:
        engine = self.seeded(siar_use_impact_efficiency=False)
        bar = auction(9, 102.5, 105.0, 100.0, 104.8, 300.0, 0.8)
        transitions = engine._evaluate_completed_bias(bar, snap(10, 9, 102.5, 105.0, 100.0, 104.8, 0.8))
        self.assertTrue(transitions)
        self.assertIsNotNone(engine._bias)


if __name__ == "__main__":
    unittest.main()
