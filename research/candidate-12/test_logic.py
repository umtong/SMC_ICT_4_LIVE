from __future__ import annotations

from decimal import Decimal
import unittest

from logic import (
    BarObs,
    CausalLiquidityAuctionEngine,
    ConfirmationState,
    Direction,
    LiquidityPool,
    LogicConfig,
    RiskSizer,
    ScenarioKind,
    Side,
)


class RiskSizerTests(unittest.TestCase):
    def test_three_percent_budget_is_not_exceeded_after_rounding(self) -> None:
        decision = RiskSizer(0.03).size(
            nav=Decimal("100000"),
            loss_per_unit=Decimal("125.50"),
            entry_price=Decimal("60000"),
            quantity_increment=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("10"),
            margin_init=Decimal("0.05"),
            free_balance=Decimal("100000"),
        )
        self.assertTrue(decision.feasible)
        self.assertLessEqual(decision.expected_total_loss, Decimal("3000"))
        self.assertGreater(decision.quantity, Decimal("0"))

    def test_margin_is_a_hard_venue_feasibility_check_not_a_risk_multiplier(self) -> None:
        decision = RiskSizer(0.03).size(
            nav=Decimal("100000"),
            loss_per_unit=Decimal("1"),
            entry_price=Decimal("1000"),
            quantity_increment=Decimal("1"),
            min_quantity=Decimal("1"),
            min_notional=Decimal("10"),
            margin_init=Decimal("0.05"),
            free_balance=Decimal("100"),
        )
        self.assertFalse(decision.feasible)
        self.assertEqual(decision.reason, "INSUFFICIENT_MARGIN")


class LogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = LogicConfig(
            atr_period=3,
            volume_period=3,
            flow_period=3,
            internal_pivot_wing=1,
            external_pivot_wing=1,
            external_tf_minutes=2,
            range_tf_minutes=4,
            pool_expiry_minutes=60,
            max_pools_per_side=10,
            min_pool_age_bars=1,
            min_net_r=1.0,
            price_increment=0.1,
        )
        self.engine = CausalLiquidityAuctionEngine(self.config, "BTCUSDT-PERP.BINANCE")

    def test_bar_rejects_impossible_ohlc(self) -> None:
        with self.assertRaises(ValueError):
            BarObs(1, 100, 99, 98, 100, 10, 5)

    def test_costed_plan_uses_live_structural_target(self) -> None:
        source = LiquidityPool(
            "source", Side.HIGH, 99.0, "PRIOR_4H", 1, 2, 10**18, 0,
        )
        target = LiquidityPool(
            "target", Side.HIGH, 105.0, "PRIOR_DAY", 1, 2, 10**18, 0,
        )
        self.engine._pools.extend((source, target))
        state = ConfirmationState(
            scenario_id="scenario",
            pool_id="source",
            kind=ScenarioKind.ACCEPTANCE,
            direction=Direction.LONG,
            started_index=1,
            trigger_extreme=100.0,
            structure_level=99.0,
        )
        bar = BarObs(100, 99.5, 100.2, 99.4, 100.0, 100, 60)
        plan = self.engine._costed_plan(
            state=state,
            pool=source,
            bar=bar,
            atr=1.0,
            stop_anchor=99.0,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.target_price, 105.0)
        self.assertGreater(plan.loss_per_unit, abs(plan.expected_entry - plan.stop_price))
        self.assertGreaterEqual(plan.net_r, self.config.min_net_r)

    def test_confirmed_pivot_observation_never_precedes_pivot_time(self) -> None:
        bars = [
            BarObs(60_000_000_000 * i, 100, 101 + (i % 3), 99 - (i % 2), 100, 10, 5)
            for i in range(1, 20)
        ]
        for bar in bars:
            self.engine.on_bar(bar)
        self.assertTrue(all(event.observed_time_ns >= event.event_time_ns for event in self.engine.events))
        self.assertEqual(
            [event.observed_time_ns for event in self.engine.events],
            sorted(event.observed_time_ns for event in self.engine.events),
        )


if __name__ == "__main__":
    unittest.main()
