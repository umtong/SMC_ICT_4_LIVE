from __future__ import annotations

import unittest

from absorption_structure_engine import AbsorptionConfirmedStructureReversalEngine
from hierarchical_sweep_engine import _AuctionBar
from lrb_types import BarObservation, PrimitiveSnapshot


def auction(
    end: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    flow: float,
) -> _AuctionBar:
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


def snap(
    index: int,
    timestamp: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    flow: float = 0.2,
) -> PrimitiveSnapshot:
    width = max(high - low, 0.1)
    volume = 100.0
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


class AbsorptionStructureTests(unittest.TestCase):
    def params(self, **overrides):
        values = {
            "hsc_bias_period_minutes": 30,
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
            "hff_use_bias_flow": False,
            "hff_use_sweep_flow": True,
            "hff_use_response_flow": True,
            "afhr_use_extreme_freshness": True,
            "afhr_stale_periods": 6.0,
            "siar_use_flow_surprise": False,
            "siar_use_impact_efficiency": True,
            "siar_flow_lookback": 8,
            "siar_min_history": 8,
            "siar_surprise_quantile": 0.75,
            "siar_min_efficiency_history": 4,
            "acsr_require_impact_absorption": True,
            "acsr_use_structure_flow": True,
            "acsr_confirmation_periods": 2.0,
            "acsr_structure_lookback_bars": 4,
            "acsr_structure_range_lookback": 8,
            "acsr_structure_break_range_fraction": 0.05,
            "acsr_structure_body_fraction": 0.50,
            "acsr_structure_relative_range": 0.80,
            "acsr_structure_flow_ratio": 0.04,
            "acsr_structure_close_location": 0.65,
            "acsr_disproof_extension_atr_htf": 0.02,
        }
        values.update(overrides)
        return values

    def seeded(self, **overrides):
        engine = AbsorptionConfirmedStructureReversalEngine(self.params(**overrides))
        flows = [-0.4, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, -0.3]
        closes = [98.0, 99.0, 100.0, 100.0, 101.0, 102.5, 103.5, 97.5]
        engine._bias_history = [
            auction(
                index + 1,
                100.0,
                max(104.0, close + 1.0),
                min(96.0, close - 1.0),
                close,
                200.0,
                flow,
            )
            for index, (flow, close) in enumerate(zip(flows, closes))
        ]
        engine._bias_true_ranges = [10.0] * 8
        engine._bias_volumes = [200.0] * 8
        engine._liquidity_history = [
            auction(20 + i, 102.0, 103.0 + 0.1 * i, 100.0 + 0.1 * i, 102.0, 100.0, 0.0)
            for i in range(8)
        ]
        return engine

    def arm_absorption(self, engine: AbsorptionConfirmedStructureReversalEngine) -> None:
        weak = auction(100, 102.5, 105.0, 100.0, 104.8, 300.0, 0.8)
        transitions = engine._evaluate_completed_bias(
            weak,
            snap(1000, 100, 102.5, 105.0, 100.0, 104.8, 0.8),
        )
        self.assertTrue(transitions)
        self.assertIsNotNone(engine._absorption_anchor)
        self.assertIsNone(engine._bias)

    def test_impact_inefficient_breakout_arms_but_does_not_trade_immediately(self) -> None:
        engine = self.seeded()
        self.arm_absorption(engine)
        assert engine._absorption_anchor is not None
        self.assertEqual(engine._absorption_anchor.source_direction, "LONG")
        self.assertEqual(engine._absorption_anchor.reversal_direction, "SHORT")

    def test_same_completed_bar_cannot_self_confirm_reversal(self) -> None:
        engine = self.seeded()
        self.arm_absorption(engine)
        current = auction(100, 102.0, 102.5, 98.0, 98.2, 180.0, -0.5)
        transitions = engine._evaluate_completed_reversal_structure(
            current,
            snap(1000, 100, 102.0, 102.5, 98.0, 98.2, -0.5),
        )
        self.assertEqual(transitions, ())
        self.assertIsNone(engine._bias)
        self.assertIsNotNone(engine._absorption_anchor)

    def test_later_opposite_structure_break_creates_reversal_bias(self) -> None:
        engine = self.seeded()
        self.arm_absorption(engine)
        current = auction(101, 102.0, 102.4, 98.0, 98.1, 180.0, -0.5)
        transitions = engine._evaluate_completed_reversal_structure(
            current,
            snap(1005, 101, 102.0, 102.4, 98.0, 98.1, -0.5),
        )
        self.assertEqual(len(transitions), 1)
        self.assertEqual(
            transitions[0].reason_code,
            "ABSORPTION_FOLLOWED_BY_CONFIRMED_OPPOSITE_STRUCTURE_BREAK",
        )
        self.assertIsNone(engine._absorption_anchor)
        self.assertIsNotNone(engine._bias)
        assert engine._bias is not None
        self.assertEqual(engine._bias.direction, "SHORT")
        self.assertLess(engine._bias.close, engine._bias.boundary)
        self.assertIn(engine._bias.context_id, engine._acsr_by_context)

    def test_structure_flow_is_a_single_variable_ablation(self) -> None:
        full = self.seeded()
        self.arm_absorption(full)
        price_only_break = auction(101, 102.0, 102.4, 98.0, 98.1, 180.0, 0.2)
        full._evaluate_completed_reversal_structure(
            price_only_break,
            snap(1005, 101, 102.0, 102.4, 98.0, 98.1, 0.2),
        )
        self.assertIsNone(full._bias)

        ablated = self.seeded(acsr_use_structure_flow=False)
        self.arm_absorption(ablated)
        ablated._evaluate_completed_reversal_structure(
            price_only_break,
            snap(1005, 101, 102.0, 102.4, 98.0, 98.1, 0.2),
        )
        self.assertIsNotNone(ablated._bias)

    def test_directional_acceptance_disproves_absorption_before_reversal(self) -> None:
        engine = self.seeded()
        self.arm_absorption(engine)
        continuation = auction(101, 104.8, 106.0, 104.5, 105.9, 180.0, 0.5)
        transitions = engine._evaluate_completed_reversal_structure(
            continuation,
            snap(1005, 101, 104.8, 106.0, 104.5, 105.9, 0.5),
        )
        self.assertEqual(len(transitions), 1)
        self.assertEqual(
            transitions[0].reason_code,
            "ABSORPTION_DISPROVED_BY_DIRECTIONAL_ACCEPTANCE",
        )
        self.assertIsNone(engine._absorption_anchor)
        self.assertIsNone(engine._bias)


if __name__ == "__main__":
    unittest.main()
