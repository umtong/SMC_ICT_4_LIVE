from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from state_engine import EngineConfig, FlowBar, LiquidityStateEngine, risk_based_quantity

MINUTE = 60_000_000_000


def bar(minute, open_, high, low, close, volume=100.0, buy_fraction=0.5):
    return FlowBar(minute * MINUTE, open_, high, low, close, volume, volume * buy_fraction, 100)


def config(**overrides):
    values = dict(
        auction_horizons_minutes=(4,), atr_period=2, volume_period=2,
        approach_period=2, maximum_active_levels_per_side=8,
        maximum_level_age_minutes=60, minimum_breach_atr=0.0,
        cluster_tolerance_atr=0.05, acceptance_buffer_atr=0.0,
        acceptance_closes=2, acceptance_timeout_bars=5, retest_timeout_bars=4,
        post_retest_resolution_bars=4, retest_tolerance_atr=0.3,
        defended_close_buffer_atr=0.0, failure_close_buffer_atr=0.0,
        reexpansion_buffer_atr=0.0, stop_buffer_atr=0.0,
        minimum_approach_efficiency=0.0, minimum_approach_flow=0.0,
        directional_imbalance=0.05, maximum_adverse_retest_flow=0.2,
        minimum_volume_ratio=0.5, minimum_displacement_atr=0.1,
        minimum_excursion_atr=0.1, minimum_resolution_displacement_atr=0.1,
        minimum_net_reward_to_risk=0.1, composite_cost_per_fill=0.0,
        cooldown_bars=0, use_flow_confirmation=True,
        require_acceptance_confirmation=True, require_reexpansion_confirmation=True,
        use_opposite_edge_target=True,
    )
    values.update(overrides)
    return EngineConfig(**values)


def seed(engine):
    for item in (
        bar(1, 99.0, 101.0, 98.5, 100.0, buy_fraction=0.55),
        bar(2, 100.0, 100.5, 97.0, 99.0, buy_fraction=0.55),
        bar(3, 99.0, 100.0, 95.0, 98.0, buy_fraction=0.55),
        bar(4, 98.5, 100.0, 98.2, 99.5, buy_fraction=0.65),
    ):
        engine.on_bar(item)
    engine.on_bar(bar(5, 99.5, 100.5, 99.2, 100.2, buy_fraction=0.70))


def accept_up(engine):
    engine.on_bar(bar(6, 100.2, 102.0, 100.0, 101.6, 220.0, 0.90))
    return engine.on_bar(bar(7, 101.6, 102.3, 101.2, 101.9, 190.0, 0.80))


class CausalPathsTest(unittest.TestCase):
    def test_range_is_not_available_before_completion(self):
        engine = LiquidityStateEngine(config())
        for minute in range(1, 5):
            engine.on_bar(bar(minute, 100, 101, 99, 100))
        self.assertEqual(engine.active_pools, ())
        engine.on_bar(bar(5, 100, 100.5, 99.5, 100))
        self.assertEqual(len(engine.active_pools), 2)

    def test_accepted_breakout_failure_targets_opposite_edge(self):
        engine = LiquidityStateEngine(config())
        seed(engine)
        accept_up(engine)
        result = engine.on_bar(bar(8, 101.7, 101.8, 100.4, 100.6, 200.0, 0.15))
        self.assertIsNotNone(result.signal)
        self.assertEqual(result.signal.branch, "REVERSAL")
        self.assertEqual(result.signal.side, "SELL")
        self.assertAlmostEqual(result.signal.target_price, 95.0)
        self.assertEqual(result.signal.details["target_model"], "OPPOSITE_EDGE")

    def test_midpoint_is_explicit_ablation(self):
        engine = LiquidityStateEngine(config(use_opposite_edge_target=False))
        seed(engine)
        accept_up(engine)
        result = engine.on_bar(bar(8, 101.7, 101.8, 100.4, 100.6, 200.0, 0.15))
        self.assertIsNotNone(result.signal)
        self.assertAlmostEqual(result.signal.target_price, 98.0)
        self.assertEqual(result.signal.details["target_model"], "MIDPOINT")

    def test_genuine_reexpansion_invalidates_trap_without_continuation_entry(self):
        engine = LiquidityStateEngine(config())
        seed(engine)
        accept_up(engine)
        retest = engine.on_bar(bar(8, 101.1, 101.6, 100.9, 101.3, 160.0, 0.60))
        self.assertIsNone(retest.signal)
        result = engine.on_bar(bar(9, 101.3, 102.2, 101.2, 102.0, 180.0, 0.75))
        self.assertIsNone(result.signal)
        self.assertIn(
            "GENUINE_REEXPANSION_INVALIDATED_TRAP_REVERSAL",
            {event.reason_code for event in result.events},
        )


class VariantContractTest(unittest.TestCase):
    def test_structural_variants_change_one_component(self):
        payload = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text())
        baseline = EngineConfig.from_mapping(payload, ablation="baseline")
        no_5m = EngineConfig.from_mapping(payload, ablation="no-5m")
        with_240 = EngineConfig.from_mapping(payload, ablation="with-240m")
        midpoint = EngineConfig.from_mapping(payload, ablation="midpoint-target")
        self.assertEqual(baseline.auction_horizons_minutes, (5, 15, 60, 1440))
        self.assertEqual(no_5m.auction_horizons_minutes, (15, 60, 1440))
        self.assertEqual(with_240.auction_horizons_minutes, (5, 15, 60, 240, 1440))
        self.assertTrue(baseline.use_opposite_edge_target)
        self.assertFalse(midpoint.use_opposite_edge_target)


class RiskTest(unittest.TestCase):
    def test_three_percent_loss_budget(self):
        result = risk_based_quantity(
            nav=Decimal("100000"), risk_fraction=Decimal("0.03"),
            entry_price=Decimal("50000"), stop_price=Decimal("49500"),
            cost_rate_per_fill=Decimal("0.00075"), quantity_increment=Decimal("0.001"),
        )
        self.assertLessEqual(result.planned_loss, Decimal("3000"))

    def test_over_cap_rejected(self):
        with self.assertRaises(ValueError):
            risk_based_quantity(
                nav=Decimal("100000"), risk_fraction=Decimal("0.030001"),
                entry_price=Decimal("50000"), stop_price=Decimal("49500"),
                cost_rate_per_fill=Decimal("0.00075"), quantity_increment=Decimal("0.001"),
            )


if __name__ == "__main__":
    unittest.main()
