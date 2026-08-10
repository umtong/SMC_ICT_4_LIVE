from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from core import (  # noqa: E402
    CandidateConfig,
    LiquidityCascadeEngine,
    MarketBar,
    PoolSide,
    Scenario,
    ScenarioState,
    TradeSide,
    geometric_daily_growth,
    max_drawdown,
    size_by_planned_loss,
)


class RiskSizingTest(unittest.TestCase):
    def test_quantity_never_exceeds_planned_loss_budget(self) -> None:
        result = size_by_planned_loss(
            nav=Decimal("100000"),
            risk_fraction=Decimal("0.01"),
            entry_price=Decimal("60000"),
            stop_price=Decimal("59850"),
            entry_fee_rate=Decimal("0.0005"),
            stop_fee_rate=Decimal("0.0005"),
            entry_slippage_rate=Decimal("0.0002"),
            stop_slippage_rate=Decimal("0.0003"),
            market_impact_rate=Decimal("0.0001"),
            funding_rate_allowance=Decimal("0.00005"),
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            minimum_notional=Decimal("5"),
        )
        self.assertIsNone(result.skipped_reason)
        self.assertGreater(result.quantity, 0)
        self.assertLessEqual(result.planned_loss, result.risk_budget)
        self.assertGreater(result.entry_notional, result.nav)
        # The absence of a notional cap is intentional; stop/cost loss controls risk.

    def test_exchange_minimum_can_skip_but_never_scale_up(self) -> None:
        result = size_by_planned_loss(
            nav=Decimal("10"),
            risk_fraction=Decimal("0.001"),
            entry_price=Decimal("60000"),
            stop_price=Decimal("59000"),
            entry_fee_rate=Decimal("0.0005"),
            stop_fee_rate=Decimal("0.0005"),
            entry_slippage_rate=Decimal("0"),
            stop_slippage_rate=Decimal("0"),
            market_impact_rate=Decimal("0"),
            funding_rate_allowance=Decimal("0"),
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            minimum_notional=Decimal("5"),
        )
        self.assertEqual(result.quantity, Decimal("0"))
        self.assertIn(result.skipped_reason, {"ROUNDED_TO_ZERO", "BELOW_MINIMUM_QUANTITY"})


class CausalityTest(unittest.TestCase):
    def test_out_of_order_bar_is_rejected(self) -> None:
        engine = LiquidityCascadeEngine("BTCUSDT")
        first = MarketBar("BTCUSDT", 60_000_000_000, 100, 101, 99, 100, 10)
        engine.on_bar(first)
        with self.assertRaises(ValueError):
            engine.on_bar(first)

    def test_confirmed_pivot_observation_is_later_than_pivot(self) -> None:
        cfg = CandidateConfig(
            atr_period=3,
            volume_period=3,
            internal_left=1,
            internal_right=1,
            external_left=1,
            external_right=1,
            external_minutes=1,
            warmup_bars=3,
        )
        engine = LiquidityCascadeEngine("BTCUSDT", cfg)
        bars = [
            MarketBar("BTCUSDT", i * 60_000_000_000, 100, high, low, 100, 10)
            for i, (high, low) in enumerate(
                [(101, 99), (103, 98), (101, 99), (102, 97), (101, 99)],
                start=1,
            )
        ]
        for bar in bars:
            engine.on_bar(bar)
        high_pool = next(pool for pool in engine.pools if pool.side is PoolSide.HIGH)
        self.assertGreater(high_pool.observed_time_ns, high_pool.event_time_ns)

    def test_no_signal_without_complete_state_sequence(self) -> None:
        cfg = CandidateConfig(
            atr_period=3,
            volume_period=3,
            internal_left=1,
            internal_right=1,
            external_left=1,
            external_right=1,
            external_minutes=1,
            warmup_bars=3,
        )
        engine = LiquidityCascadeEngine("BTCUSDT", cfg)
        signals = []
        for i in range(1, 30):
            price = 100 + i * 0.1
            signal = engine.on_bar(
                MarketBar("BTCUSDT", i * 60_000_000_000, price, price + 0.2, price - 0.2, price, 10),
            )
            if signal:
                signals.append(signal)
        self.assertEqual(signals, [])

    def test_target_geometry_uses_nearest_opposing_pool(self) -> None:
        cfg = CandidateConfig(atr_period=3, volume_period=3, min_reward_risk=1.0)
        engine = LiquidityCascadeEngine("BTCUSDT", cfg)
        engine._add_pool(PoolSide.LOW, 90, 1, 1, "TEST", 1.0)
        engine._add_pool(PoolSide.LOW, 80, 1, 1, "TEST", 1.0)
        scenario = Scenario(
            scenario_id="s",
            state=ScenarioState.ARMED,
            pool_id="missing",
            swept_side=PoolSide.HIGH,
            trade_side=TradeSide.SELL,
            level=105,
            extreme=106,
            structure_level=99,
            event_time_ns=1,
            observed_time_ns=1,
            start_bar_index=1,
            state_bar_index=1,
            displacement_atr=1.0,
        )
        bar = MarketBar("BTCUSDT", 2, 100, 101, 99, 100, 10)
        signal = engine._build_signal(scenario, bar, atr=1.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.target_price, 90)


class MetricTest(unittest.TestCase):
    def test_geometric_growth_and_drawdown(self) -> None:
        growth = geometric_daily_growth([1.07, 0.95, 1.12], 21)
        self.assertAlmostEqual((1 + growth) ** 21, 1.07 * 0.95 * 1.12)
        self.assertAlmostEqual(max_drawdown([100, 110, 99, 120]), -0.1)


if __name__ == "__main__":
    unittest.main()
