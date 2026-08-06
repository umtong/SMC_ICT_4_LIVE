from __future__ import annotations

import unittest
from decimal import Decimal

from state_engine import EngineConfig, FlowBar, LiquidityStateEngine, risk_based_quantity

MINUTE = 60_000_000_000


def bar(minute, open_, high, low, close, volume=100.0, buy_fraction=0.5):
    return FlowBar(
        ts_ns=minute * MINUTE,
        open=open_, high=high, low=low, close=close,
        volume=volume, taker_buy_volume=volume * buy_fraction, trade_count=100,
    )


def config(**overrides):
    values = dict(
        auction_horizons_minutes=(4,), atr_period=2, volume_period=2,
        approach_period=2, maximum_active_levels_per_side=8,
        maximum_level_age_minutes=60, minimum_breach_atr=0.0,
        cluster_tolerance_atr=0.05, acceptance_buffer_atr=0.0,
        acceptance_closes=2, resolution_timeout_bars=5, retest_timeout_bars=4,
        retest_tolerance_atr=0.3, defended_close_buffer_atr=0.0,
        stop_buffer_atr=0.0, minimum_approach_efficiency=0.0,
        minimum_approach_flow=0.0, directional_imbalance=0.05,
        maximum_adverse_retest_flow=0.2, minimum_volume_ratio=0.5,
        minimum_displacement_atr=0.1, minimum_excursion_atr=0.1,
        minimum_net_reward_to_risk=0.1, composite_cost_per_fill=0.0,
        cooldown_bars=0, use_flow_confirmation=True,
        require_acceptance_confirmation=True, require_retest_confirmation=True,
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


class CausalityTest(unittest.TestCase):
    def test_completed_range_is_observed_at_next_block_only(self):
        engine = LiquidityStateEngine(config())
        seed(engine)
        self.assertEqual(engine.active_pools, ())
        result = engine.on_bar(bar(5, 99.5, 100.5, 99.2, 100.2, buy_fraction=0.70))
        self.assertEqual(len(engine.active_pools), 2)
        self.assertEqual(
            sum(event.event_type == "EXTERNAL_LIQUIDITY_LEVEL_CONFIRMED" for event in result.events),
            2,
        )
        self.assertIsNone(result.signal)

    def test_two_sided_breach_is_no_trade(self):
        engine = LiquidityStateEngine(config())
        seed(engine)
        engine.on_bar(bar(5, 99.5, 100.5, 99.2, 100.2, buy_fraction=0.7))
        result = engine.on_bar(bar(6, 100.2, 102.0, 94.0, 99.0, volume=220.0, buy_fraction=0.5))
        self.assertIsNone(result.signal)
        self.assertIn("AMBIGUOUS_TWO_SIDED_BREACH", {event.event_type for event in result.events})


class ContinuationPathTest(unittest.TestCase):
    def test_approach_acceptance_retest_emits_long(self):
        engine = LiquidityStateEngine(config())
        seed(engine)
        engine.on_bar(bar(5, 99.5, 100.5, 99.2, 100.2, buy_fraction=0.70))
        breach = engine.on_bar(bar(6, 100.2, 102.0, 100.0, 101.6, volume=220.0, buy_fraction=0.90))
        self.assertIsNone(breach.signal)
        accepted = engine.on_bar(bar(7, 101.6, 102.3, 101.2, 101.9, volume=190.0, buy_fraction=0.80))
        self.assertIsNone(accepted.signal)
        result = engine.on_bar(bar(8, 101.1, 101.6, 100.9, 101.3, volume=160.0, buy_fraction=0.60))
        self.assertIsNotNone(result.signal)
        self.assertEqual(result.signal.branch, "CONTINUATION")
        self.assertEqual(result.signal.side, "BUY")
        self.assertLess(result.signal.stop_price, result.signal.entry_reference)
        self.assertGreater(result.signal.target_price, result.signal.entry_reference)

    def test_no_retest_ablation_enters_on_acceptance(self):
        engine = LiquidityStateEngine(config(require_retest_confirmation=False))
        seed(engine)
        engine.on_bar(bar(5, 99.5, 100.5, 99.2, 100.2, buy_fraction=0.70))
        engine.on_bar(bar(6, 100.2, 102.0, 100.0, 101.6, volume=220.0, buy_fraction=0.90))
        result = engine.on_bar(bar(7, 101.6, 102.3, 101.2, 101.9, volume=190.0, buy_fraction=0.80))
        self.assertIsNotNone(result.signal)
        self.assertEqual(result.signal.side, "BUY")


class RiskSizingTest(unittest.TestCase):
    def test_full_expected_loss_is_floored_below_three_percent(self):
        result = risk_based_quantity(
            nav=Decimal("100000"), risk_fraction=Decimal("0.03"),
            entry_price=Decimal("50000"), stop_price=Decimal("49500"),
            cost_rate_per_fill=Decimal("0.00075"), quantity_increment=Decimal("0.001"),
        )
        self.assertLessEqual(result.planned_loss, Decimal("3000"))
        self.assertGreater(result.per_unit_expected_loss, Decimal("500"))

    def test_risk_above_cap_is_rejected(self):
        with self.assertRaises(ValueError):
            risk_based_quantity(
                nav=Decimal("100000"), risk_fraction=Decimal("0.030001"),
                entry_price=Decimal("50000"), stop_price=Decimal("49500"),
                cost_rate_per_fill=Decimal("0.00075"), quantity_increment=Decimal("0.001"),
            )


if __name__ == "__main__":
    unittest.main()
