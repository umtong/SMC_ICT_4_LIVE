from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_engine import EngineConfig, FlowBar, LiquidityStateEngine, risk_based_quantity


MINUTE = 60_000_000_000


def bar(
    minute: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    volume: float = 100.0,
    buy_fraction: float = 0.5,
) -> FlowBar:
    return FlowBar(
        ts_ns=minute * MINUTE,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=volume * buy_fraction,
        trade_count=100,
    )


def config(**overrides) -> EngineConfig:
    values = dict(
        auction_block_minutes=4,
        atr_period=2,
        volume_period=2,
        pressure_period=2,
        mss_lookback_bars=1,
        maximum_active_ranges=4,
        maximum_range_age_blocks=3,
        minimum_breach_atr=0.0,
        reclaim_buffer_atr=0.0,
        acceptance_buffer_atr=0.0,
        acceptance_closes=2,
        resolution_timeout_bars=8,
        retest_timeout_bars=4,
        retest_tolerance_atr=0.3,
        stop_buffer_atr=0.0,
        directional_imbalance=0.05,
        cumulative_flow_imbalance=0.0,
        minimum_volume_ratio=0.5,
        minimum_displacement_atr=0.1,
        absorption_max_progress_atr=0.5,
        absorption_min_wick_atr=0.1,
        minimum_approach_efficiency=0.0,
        minimum_mss_displacement_atr=0.1,
        minimum_net_reward_to_risk=0.1,
        composite_cost_per_fill=0.0,
        cooldown_bars=0,
        use_flow_confirmation=True,
        require_mss_confirmation=True,
        require_acceptance_confirmation=True,
    )
    values.update(overrides)
    return EngineConfig(**values)


def seed_range(engine: LiquidityStateEngine) -> None:
    observations = [
        bar(1, 99.0, 101.0, 98.5, 100.0, buy_fraction=0.60),
        bar(2, 100.0, 100.5, 97.0, 99.0, buy_fraction=0.55),
        bar(3, 99.0, 100.0, 95.0, 98.0, buy_fraction=0.55),
        bar(4, 98.5, 100.0, 98.2, 99.5, buy_fraction=0.65),
    ]
    for item in observations:
        engine.on_bar(item)


class AnchoredRangeCausalityTest(unittest.TestCase):
    def test_range_is_observed_only_after_block_completion(self):
        engine = LiquidityStateEngine(config())
        seed_range(engine)
        self.assertEqual(engine.active_pools, ())
        result = engine.on_bar(bar(5, 99.5, 100.0, 99.0, 99.8))
        self.assertEqual(len(engine.active_pools), 1)
        self.assertIn("DEALING_RANGE_CONFIRMED", {event.event_type for event in result.events})
        auction = engine.active_pools[0]
        self.assertEqual(auction.high, 101.0)
        self.assertEqual(auction.low, 95.0)

    def test_same_bar_two_sided_breach_is_no_trade(self):
        engine = LiquidityStateEngine(config())
        seed_range(engine)
        result = engine.on_bar(bar(5, 99.5, 102.0, 94.0, 99.0, volume=200.0, buy_fraction=0.5))
        self.assertIsNone(result.signal)
        self.assertIn("AMBIGUOUS_TWO_SIDED_BREACH", {event.event_type for event in result.events})


class StatePathTest(unittest.TestCase):
    def test_absorption_reclaim_and_mss_emit_short(self):
        engine = LiquidityStateEngine(config())
        seed_range(engine)
        engine.on_bar(bar(5, 99.5, 102.0, 99.0, 100.4, volume=220.0, buy_fraction=0.90))
        reclaim = engine.on_bar(bar(6, 100.4, 100.6, 98.0, 98.4, volume=180.0, buy_fraction=0.20))
        self.assertIsNone(reclaim.signal)
        result = engine.on_bar(bar(7, 98.4, 98.6, 97.2, 97.5, volume=170.0, buy_fraction=0.15))
        self.assertIsNotNone(result.signal)
        self.assertEqual(result.signal.branch, "REVERSAL")
        self.assertEqual(result.signal.side, "SELL")
        self.assertLess(result.signal.target_price, result.signal.entry_reference)
        self.assertGreater(result.signal.stop_price, result.signal.entry_reference)

    def test_displacement_acceptance_and_defended_retest_emit_long(self):
        engine = LiquidityStateEngine(config())
        seed_range(engine)
        engine.on_bar(bar(5, 99.5, 102.0, 99.4, 101.6, volume=220.0, buy_fraction=0.90))
        accepted = engine.on_bar(bar(6, 101.6, 102.2, 101.2, 101.8, volume=190.0, buy_fraction=0.80))
        self.assertIsNone(accepted.signal)
        result = engine.on_bar(bar(7, 101.0, 101.6, 100.9, 101.3, volume=160.0, buy_fraction=0.60))
        self.assertIsNotNone(result.signal)
        self.assertEqual(result.signal.branch, "CONTINUATION")
        self.assertEqual(result.signal.side, "BUY")
        self.assertGreater(result.signal.target_price, result.signal.entry_reference)
        self.assertLess(result.signal.stop_price, result.signal.entry_reference)


class RiskSizingTest(unittest.TestCase):
    def test_full_cost_and_floor_keep_planned_loss_below_three_percent(self):
        result = risk_based_quantity(
            nav=Decimal("100000"),
            risk_fraction=Decimal("0.03"),
            entry_price=Decimal("50000"),
            stop_price=Decimal("49500"),
            cost_rate_per_fill=Decimal("0.00075"),
            quantity_increment=Decimal("0.001"),
        )
        self.assertLessEqual(result.planned_loss, Decimal("3000"))
        self.assertGreater(result.per_unit_expected_loss, Decimal("500"))

    def test_risk_above_three_percent_is_rejected(self):
        with self.assertRaises(ValueError):
            risk_based_quantity(
                nav=Decimal("100000"),
                risk_fraction=Decimal("0.0300001"),
                entry_price=Decimal("50000"),
                stop_price=Decimal("49500"),
                cost_rate_per_fill=Decimal("0.00075"),
                quantity_increment=Decimal("0.001"),
            )


if __name__ == "__main__":
    unittest.main()
