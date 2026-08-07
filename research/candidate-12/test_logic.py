from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from logic import (
    BarObs,
    CausalLiquidityAuctionEngine,
    Direction,
    FiveBar,
    LogicConfig,
    RiskSizer,
    ScenarioKind,
)

NS_MINUTE = 60_000_000_000


def ts(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


class RiskSizerTests(unittest.TestCase):
    def test_three_percent_budget_is_not_exceeded_after_rounding(self) -> None:
        decision = RiskSizer(0.03).size(
            nav=Decimal("100000"),
            loss_per_unit=Decimal("174.61"),
            entry_price=Decimal("30580"),
            quantity_increment=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("10"),
            margin_init=Decimal("0.05"),
            free_balance=Decimal("100000"),
        )
        self.assertTrue(decision.feasible)
        self.assertLessEqual(decision.expected_total_loss, Decimal("3000"))

    def test_margin_is_a_feasibility_check_not_a_risk_multiplier(self) -> None:
        decision = RiskSizer(0.03).size(
            nav=Decimal("100000"),
            loss_per_unit=Decimal("10"),
            entry_price=Decimal("100000"),
            quantity_increment=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("10"),
            margin_init=Decimal("0.05"),
            free_balance=Decimal("100"),
        )
        self.assertFalse(decision.feasible)
        self.assertEqual(decision.reason, "INSUFFICIENT_MARGIN")


class LogicTests(unittest.TestCase):
    def test_bar_rejects_impossible_ohlc(self) -> None:
        with self.assertRaises(ValueError):
            BarObs(1, 100, 99, 98, 100, 1, 0.5)

    def test_risk_fraction_above_three_percent_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LogicConfig(risk_fraction=0.031)

    def test_london_range_is_not_frozen_before_1200(self) -> None:
        engine = CausalLiquidityAuctionEngine(LogicConfig(atr_period=2), "BTCUSDT-PERP.BINANCE")
        for minute in range(1, 720):
            price = 100.0
            engine.on_bar(BarObs(minute * NS_MINUTE, price, 101, 99, price, 10, 5))
        self.assertFalse(any(event.event_type == "LONDON_RANGE_FROZEN" for event in engine.events))
        engine.on_bar(BarObs(720 * NS_MINUTE, 100, 105, 95, 100, 10, 5))
        frozen = [event for event in engine.events if event.event_type == "LONDON_RANGE_FROZEN"]
        self.assertEqual(len(frozen), 1)
        self.assertEqual(frozen[0].observed_time_ns, 720 * NS_MINUTE)

    def _seed_london(self, engine: CausalLiquidityAuctionEngine, day: tuple[int, int, int]) -> None:
        year, month, date = day
        for minute in range(365, 721, 5):
            high = 105.0 if minute == 720 else 104.0
            low = 95.0 if minute == 715 else 96.0
            engine._on_five(
                FiveBar(ts(year, month, date, minute // 60, minute % 60), 100, high, low, 100, 10, 5),
                True,
            )

    def test_weekend_does_not_arm_institutional_session_scenario(self) -> None:
        config = LogicConfig(atr_period=2, min_net_r=0.1)
        engine = CausalLiquidityAuctionEngine(config, "BTCUSDT-PERP.BINANCE")
        self._seed_london(engine, (2023, 1, 7))
        engine._on_five(FiveBar(ts(2023, 1, 7, 12, 5), 104, 106, 103, 104, 10, 5), True)
        self.assertEqual(dict(engine.scenario_counts), {})

    def test_causal_london_high_raid_emits_short_limit_plan(self) -> None:
        config = LogicConfig(
            atr_period=2,
            reclaim_max_bars=3,
            confirmation_bars=1,
            entry_expiry_minutes=15,
            stop_buffer_atr=0.8,
            target_range_fraction=0.6,
            min_net_r=0.1,
        )
        engine = CausalLiquidityAuctionEngine(config, "BTCUSDT-PERP.BINANCE")
        self._seed_london(engine, (2023, 1, 2))
        first = engine._on_five(
            FiveBar(ts(2023, 1, 2, 12, 5), 104.5, 106.0, 103.5, 104.0, 20, 8),
            True,
        )
        self.assertIsNone(first)
        plan = engine._on_five(
            FiveBar(ts(2023, 1, 2, 12, 10), 104.0, 105.5, 103.8, 104.5, 20, 8),
            True,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.NY_LONDON_HIGH_RAID)
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.expected_entry, 105.0)
        self.assertGreater(plan.stop_price, 106.0)
        self.assertAlmostEqual(plan.target_price, 99.0)
        self.assertEqual(plan.expire_ts_ns, plan.observed_ts_ns + 15 * NS_MINUTE)
        self.assertGreater(plan.net_r, 0)
        self.assertTrue(all(event.observed_time_ns >= event.event_time_ns for event in engine.events))

    def test_reclaim_must_arrive_before_source_is_reused(self) -> None:
        config = LogicConfig(atr_period=2, reclaim_max_bars=2, min_net_r=0.1)
        engine = CausalLiquidityAuctionEngine(config, "BTCUSDT-PERP.BINANCE")
        self._seed_london(engine, (2023, 1, 2))
        for minute in (725, 730, 735):
            engine._on_five(
                FiveBar(ts(2023, 1, 2, minute // 60, minute % 60), 106, 107, 105.1, 106.5, 10, 5),
                True,
            )
        self.assertEqual(engine.skips["RAID_NOT_RECLAIMED_IN_TIME"], 1)
        count = engine.scenario_counts[ScenarioKind.NY_LONDON_HIGH_RAID.value]
        engine._on_five(FiveBar(ts(2023, 1, 2, 12, 40), 106, 108, 104, 104.5, 10, 5), True)
        self.assertEqual(engine.scenario_counts[ScenarioKind.NY_LONDON_HIGH_RAID.value], count)


if __name__ == "__main__":
    unittest.main()
