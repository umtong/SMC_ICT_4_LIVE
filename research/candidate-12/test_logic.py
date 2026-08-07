from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from logic import (
    BarObs, CausalLiquidityAuctionEngine, Direction, FiveBar, LogicConfig,
    RiskSizer, ScenarioKind, SessionLabel,
)

NS_MINUTE = 60_000_000_000


def ts(y: int, m: int, d: int, h: int, minute: int) -> int:
    return int(datetime(y, m, d, h, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def bar(t: int, o: float, h: float, l: float, c: float) -> FiveBar:
    return FiveBar(t, o, h, l, c, 10.0, 5.0)


def config(**kwargs: object) -> LogicConfig:
    values = {
        "atr_period": 2,
        "min_net_r": 0.0,
        "reclaim_body_atr": 0.8,
        "asia_confirmation_body_atr": 0.5,
    }
    values.update(kwargs)
    return LogicConfig(**values)


class RiskTests(unittest.TestCase):
    def test_three_percent_budget_is_not_exceeded_after_rounding(self) -> None:
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
        self.assertLessEqual(decision.expected_total_loss, Decimal("3000"))


class LogicTests(unittest.TestCase):
    DAY = (2024, 4, 15)  # Monday

    def seed_day(self, engine: CausalLiquidityAuctionEngine) -> None:
        y, m, d = self.DAY
        # Two completed bars establish ATR, then completed Asia and London ranges.
        engine._on_five(bar(ts(y, m, d, 0, 5), 100, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 0, 10), 100, 102, 98, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 0), 100, 105, 95, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 5), 100, 103, 97, 100), True)
        engine._on_five(bar(ts(y, m, d, 12, 0), 100, 105, 95, 100), True)

    def test_bar_validation_rejects_impossible_ohlc(self) -> None:
        with self.assertRaises(ValueError):
            BarObs(1, 100, 99, 98, 100, 1, 0.5)

    def test_session_range_is_not_frozen_before_completion(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "BTCUSDT-PERP.BINANCE")
        engine._on_five(bar(ts(y, m, d, 0, 5), 100, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 5, 55), 100, 105, 95, 100), True)
        self.assertNotIn(SessionLabel.ASIA, engine._ranges)
        engine._on_five(bar(ts(y, m, d, 6, 0), 100, 104, 96, 100), True)
        self.assertIn(SessionLabel.ASIA, engine._ranges)
        self.assertEqual(engine._ranges[SessionLabel.ASIA].observed_ts_ns, ts(y, m, d, 6, 0))

    def test_forceful_london_high_reclaim_emits_short_plan(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_day(engine)
        engine._on_five(bar(ts(y, m, d, 12, 5), 104, 108, 103, 107), True)
        engine._on_five(bar(ts(y, m, d, 12, 10), 107, 109, 96, 98), True)
        plan = engine._on_five(bar(ts(y, m, d, 12, 15), 100, 103, 100, 102), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.LONDON_HIGH_REJECTION)
        self.assertEqual(plan.direction, Direction.SHORT)

    def test_weak_london_reclaim_is_terminal_not_acceptance_trade(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(reclaim_body_atr=1.0), "X")
        self.seed_day(engine)
        engine._on_five(bar(ts(y, m, d, 12, 5), 104, 108, 103, 107), True)
        engine._on_five(bar(ts(y, m, d, 12, 10), 104, 106, 103, 104.5), True)
        plan = engine._on_five(bar(ts(y, m, d, 12, 15), 104.5, 106, 103, 104), True)
        self.assertIsNone(plan)
        self.assertEqual(engine.skips["RECLAIM_LACKED_DISPLACEMENT"], 1)

    def test_asia_high_rejection_requires_bearish_displacement_confirmation(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        # Freeze Asia only.
        engine._on_five(bar(ts(y, m, d, 0, 5), 100, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 0, 10), 100, 102, 98, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 0), 100, 105, 90, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 5), 125, 126, 96, 100), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 10), 100, 104, 99, 103), True)
        self.assertIsNone(plan)
        self.assertEqual(engine.skips["ASIA_REJECTION_LACKED_DOWNSIDE_CONFIRMATION"], 1)

    def test_asia_high_rejection_with_downside_confirmation_emits(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        engine._on_five(bar(ts(y, m, d, 0, 5), 100, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 0, 10), 100, 102, 98, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 0), 100, 105, 90, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 5), 125, 126, 96, 100), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 10), 110, 111, 97, 99), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.ASIA_HIGH_REJECTION)

    def test_target_consumed_before_decision_cannot_be_reused(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_day(engine)
        engine._on_five(bar(ts(y, m, d, 12, 5), 104, 108, 103, 107), True)
        engine._on_five(bar(ts(y, m, d, 12, 10), 107, 109, 96, 98), True)
        plan = engine._on_five(bar(ts(y, m, d, 12, 15), 100, 103, 98, 102), True)
        self.assertIsNone(plan)
        self.assertEqual(engine.skips["STRUCTURAL_TARGET_REACHED_BEFORE_DECISION"], 1)

    def test_low_raid_is_diagnostic_only(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_day(engine)
        plan = engine._on_five(bar(ts(y, m, d, 12, 5), 100, 102, 90, 96), True)
        self.assertIsNone(plan)
        self.assertTrue(any(event.event_type == "SESSION_LOW_RAID_DIAGNOSTIC" for event in engine.events))

    def test_completed_session_cannot_seed_second_trade(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_day(engine)
        engine._on_five(bar(ts(y, m, d, 12, 5), 104, 108, 103, 107), True)
        engine._on_five(bar(ts(y, m, d, 12, 10), 107, 109, 96, 98), True)
        self.assertIsNotNone(engine._on_five(bar(ts(y, m, d, 12, 15), 100, 103, 100, 102), True))
        count = dict(engine.scenario_counts)
        self.assertIsNone(engine._on_five(bar(ts(y, m, d, 12, 20), 102, 110, 101, 104), True))
        self.assertEqual(dict(engine.scenario_counts), count)

    def test_weekend_does_not_arm_episode(self) -> None:
        y, m, d = (2024, 4, 13)  # Saturday
        engine = CausalLiquidityAuctionEngine(config(), "X")
        engine._on_five(bar(ts(y, m, d, 0, 5), 100, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 0, 10), 100, 102, 98, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 0), 100, 105, 95, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 5), 104, 108, 103, 104), True)
        self.assertFalse(engine.scenario_counts)


if __name__ == "__main__":
    unittest.main()
