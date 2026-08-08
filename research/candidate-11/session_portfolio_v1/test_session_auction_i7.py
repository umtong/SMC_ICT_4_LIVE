from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from session_auction_i7 import (
    CausalLiquidityAuctionEngine,
    Direction,
    EntryOrder,
    FiveBar,
    LogicConfig,
    RiskSizer,
    ScenarioKind,
    SessionLabel,
)


def ts(y: int, m: int, d: int, h: int, minute: int) -> int:
    return int(datetime(y, m, d, h, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def bar(t: int, o: float, h: float, l: float, c: float) -> FiveBar:
    return FiveBar(t, o, h, l, c, 10.0, 5.0)


def diagnostic_config(**updates: object) -> LogicConfig:
    values: dict[str, object] = {
        "atr_period": 2,
        "min_net_r": 0.0,
        "rejection_stop_buffer_atr": 0.2,
        "fvg_stop_buffer_atr": 0.1,
        "rejection_reclaim_body_atr": 0.2,
        "asia_high_confirmation_body_atr": 0.2,
        "low_confirmation_body_atr": 0.2,
        "acceptance_displacement_body_atr": 0.2,
        "reacceptance_displacement_body_atr": 0.2,
        "active_retest_body_atr": 0.1,
        "passive_retest_body_atr": 0.2,
    }
    values.update(updates)
    return LogicConfig(**values)


class FrozenSessionI7Tests(unittest.TestCase):
    DAY = (2024, 5, 15)

    def seed_asia(self, engine: CausalLiquidityAuctionEngine) -> None:
        y, m, d = self.DAY
        engine._on_five(bar(ts(y, m, d, 0, 5), 100, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 0, 10), 100, 102, 98, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 0), 100, 105, 95, 100), True)

    def test_exact_three_percent_sizing_budget(self):
        decision = RiskSizer(0.03).size(
            nav=Decimal("100000"),
            loss_per_unit=Decimal("83.17"),
            entry_price=Decimal("30000"),
            quantity_increment=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("10"),
            margin_init=Decimal("0.05"),
            free_balance=Decimal("100000"),
        )
        self.assertTrue(decision.feasible)
        self.assertEqual(decision.planned_loss_budget, Decimal("3000.00"))
        self.assertLessEqual(decision.expected_total_loss, decision.planned_loss_budget)

    def test_asia_range_is_frozen_only_after_completion(self):
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(diagnostic_config(), "BTCUSDT-PERP.BINANCE")
        engine._on_five(bar(ts(y, m, d, 0, 5), 100, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 5, 55), 100, 105, 95, 100), True)
        self.assertNotIn(SessionLabel.ASIA, engine._sources)
        engine._on_five(bar(ts(y, m, d, 6, 0), 100, 104, 96, 100), True)
        self.assertIn(SessionLabel.ASIA, engine._sources)

    def test_premium_side_asia_high_failed_auction_emits_short_market(self):
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(diagnostic_config(), "BTCUSDT-PERP.BINANCE")
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 107, 108, 99.5, 100), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 10), 103, 103.5, 99.5, 100), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.ASIA_HIGH_REJECTION)
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.entry_order, EntryOrder.MARKET)

    def test_same_completed_source_cannot_emit_second_plan(self):
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(diagnostic_config(), "BTCUSDT-PERP.BINANCE")
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 107, 108, 99.5, 100), True)
        first = engine._on_five(bar(ts(y, m, d, 6, 10), 103, 103.5, 99.5, 100), True)
        self.assertIsNotNone(first)
        state = engine._sources[SessionLabel.ASIA]
        self.assertTrue(state.trade_plan_emitted)
        second = engine._on_five(bar(ts(y, m, d, 6, 15), 96, 97, 90, 96), True)
        self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
