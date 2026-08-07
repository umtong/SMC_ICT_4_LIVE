from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from logic import (
    BarObs,
    CausalLiquidityAuctionEngine,
    Direction,
    EntryOrder,
    FiveBar,
    LogicConfig,
    RiskSizer,
    ScenarioKind,
    SessionLabel,
)

NS_MINUTE = 60_000_000_000


def ts(y: int, m: int, d: int, h: int, minute: int) -> int:
    return int(datetime(y, m, d, h, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def bar(t: int, o: float, h: float, l: float, c: float) -> FiveBar:
    return FiveBar(t, o, h, l, c, 10.0, 5.0)


def config(**kwargs: object) -> LogicConfig:
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
    DAY = (2024, 5, 15)  # Wednesday

    def seed_asia(self, engine: CausalLiquidityAuctionEngine) -> None:
        y, m, d = self.DAY
        engine._on_five(bar(ts(y, m, d, 0, 5), 100, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 0, 10), 100, 102, 98, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 0), 100, 105, 95, 100), True)

    def seed_london(self, engine: CausalLiquidityAuctionEngine) -> None:
        self.seed_asia(engine)
        y, m, d = self.DAY
        engine._on_five(bar(ts(y, m, d, 6, 5), 100, 103, 97, 100), True)
        engine._on_five(bar(ts(y, m, d, 12, 0), 100, 105, 95, 100), True)

    def form_asia_high_acceptance(self, engine: CausalLiquidityAuctionEngine) -> None:
        y, m, d = self.DAY
        # first, bullish displacement, and current bar form a causal three-bar FVG.
        engine._on_five(bar(ts(y, m, d, 6, 5), 103, 104, 102, 103), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 103, 109, 103, 108), True)
        engine._on_five(bar(ts(y, m, d, 6, 15), 108, 109, 105.5, 108), True)

    def test_bar_validation_rejects_impossible_ohlc(self) -> None:
        with self.assertRaises(ValueError):
            BarObs(1, 100, 99, 98, 100, 1, 0.5)

    def test_session_range_is_not_frozen_before_completion(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        engine._on_five(bar(ts(y, m, d, 0, 5), 100, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 5, 55), 100, 105, 95, 100), True)
        self.assertNotIn(SessionLabel.ASIA, engine._sources)
        engine._on_five(bar(ts(y, m, d, 6, 0), 100, 104, 96, 100), True)
        self.assertIn(SessionLabel.ASIA, engine._sources)
        self.assertEqual(
            engine._sources[SessionLabel.ASIA].source.observed_ts_ns,
            ts(y, m, d, 6, 0),
        )

    def test_asia_high_raid_after_deep_discount_traverse_is_not_rejection(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        # Completed Asia range is 95-105. Price first reprices below its 25% quartile.
        engine._on_five(bar(ts(y, m, d, 6, 5), 100, 101, 96, 97), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 97, 106, 96.5, 100), True)
        state = engine._sources[SessionLabel.ASIA]
        self.assertTrue(state.high_rejection_done)
        self.assertIsNone(state.high_rejection)
        self.assertEqual(engine.skips["ASIA_HIGH_RAID_AFTER_DEEP_DISCOUNT_TRAVERSAL"], 1)

    def test_premium_side_asia_high_failed_auction_emits_short(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 107, 108, 99.5, 100), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 10), 103, 103.5, 99.5, 100), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.ASIA_HIGH_REJECTION)
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.entry_order, EntryOrder.MARKET)

    def test_low_rejection_requires_bullish_mss_above_reclaim_high(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 89, 98, 88, 97), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 10), 96, 99, 95, 97.8), True)
        self.assertIsNone(plan)
        self.assertEqual(engine.skips["LOW_REJECTION_LACKED_BULLISH_MSS"], 1)

    def test_low_rejection_with_bullish_mss_emits_long(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 89, 98, 88, 97), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 10), 96, 100, 95, 99), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.ASIA_LOW_REJECTION)
        self.assertEqual(plan.direction, Direction.LONG)

    def test_active_fvg_retest_emits_market_acceptance(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        self.form_asia_high_acceptance(engine)
        plan = engine._on_five(bar(ts(y, m, d, 6, 20), 105.4, 108, 105.2, 107.6), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.ASIA_HIGH_ACCEPTANCE)
        self.assertEqual(plan.entry_order, EntryOrder.MARKET)

    def test_forceful_passive_fvg_mitigation_emits_one_bar_gtd_limit(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        self.form_asia_high_acceptance(engine)
        decision_ts = ts(y, m, d, 6, 20)
        plan = engine._on_five(bar(decision_ts, 108, 108.2, 105.2, 105.7), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.entry_order, EntryOrder.LIMIT_GTD)
        self.assertEqual(plan.expire_ts_ns, decision_ts + 5 * NS_MINUTE)
        self.assertAlmostEqual(plan.expected_entry, 105.5)

    def test_weak_first_mitigation_can_enable_fresh_asia_reacceptance(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        self.form_asia_high_acceptance(engine)
        # First FVG mitigation is weak: no trade, but the acceptance attempt is consumed.
        self.assertIsNone(
            engine._on_five(bar(ts(y, m, d, 6, 20), 106, 107, 105.2, 106.1), True)
        )
        self.assertEqual(engine.skips["FVG_RETEST_NOT_EXECUTABLE"], 1)
        # Completed close back inside, followed by a fresh two-close FVG re-acceptance.
        engine._on_five(bar(ts(y, m, d, 6, 25), 106.1, 106.2, 104, 104.5), True)
        engine._on_five(bar(ts(y, m, d, 6, 30), 104.5, 109, 104.4, 108.5), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 35), 108.5, 110, 106.5, 109), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.ASIA_HIGH_REACCEPTANCE)
        self.assertEqual(plan.entry_order, EntryOrder.MARKET)


    def test_fvg_lower_edge_breach_waits_for_fresh_reacceleration_limit(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        # Initial acceptance peaks well above the first FVG.
        engine._on_five(bar(ts(y, m, d, 6, 5), 103, 104, 102, 103), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 103, 112, 103, 110), True)
        engine._on_five(bar(ts(y, m, d, 6, 15), 110, 115, 105.5, 110), True)
        # The first mitigation trades through the FVG lower edge, so no falling-knife limit.
        self.assertIsNone(
            engine._on_five(bar(ts(y, m, d, 6, 20), 108, 108.2, 103.5, 105.2), True)
        )
        state = engine._sources[SessionLabel.ASIA]
        self.assertEqual(state.acceptance_phase, "WAIT_REACCELERATION")
        self.assertEqual(engine.skips["INITIAL_FVG_LOWER_EDGE_BREACHED"], 1)
        # A fresh bullish displacement/FVG then permits a one-bar protected limit
        # whose structural objective is the prior acceptance expansion high.
        engine._on_five(bar(ts(y, m, d, 6, 25), 105.2, 106, 104.8, 105.5), True)
        engine._on_five(bar(ts(y, m, d, 6, 30), 105.5, 109, 105.4, 108.5), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 35), 108.5, 110, 106.5, 109), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.entry_order, EntryOrder.LIMIT_GTD)
        self.assertEqual(plan.details["target_semantics"], "PRIOR_ACCEPTANCE_EXPANSION_HIGH")
        self.assertAlmostEqual(plan.target_price, 115.0)

    def test_completed_source_cannot_emit_second_trade_plan(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 107, 108, 99.5, 100), True)
        first = engine._on_five(bar(ts(y, m, d, 6, 10), 103, 103.5, 99.5, 100), True)
        self.assertIsNotNone(first)
        state = engine._sources[SessionLabel.ASIA]
        self.assertTrue(state.trade_plan_emitted)
        # A later opposite-boundary interaction from the same completed range is consumed.
        self.assertIsNone(
            engine._on_five(bar(ts(y, m, d, 6, 15), 96, 97, 90, 96), True)
        )
        self.assertIsNone(state.low_rejection)

    def test_london_failed_acceptance_cannot_use_asia_reacceptance_route(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_london(engine)
        # Force the London source into a failed-acceptance state, then present a fresh FVG.
        state = engine._sources[SessionLabel.LONDON]
        state.failed_high_acceptance = True
        engine._on_five(bar(ts(y, m, d, 12, 5), 103, 104, 102, 103), True)
        engine._on_five(bar(ts(y, m, d, 12, 10), 103, 109, 103, 108), True)
        plan = engine._on_five(bar(ts(y, m, d, 12, 15), 108, 109, 105.5, 108), True)
        self.assertIsNone(plan)
        self.assertFalse(any(
            event.details.get("route") == "FAILED_FIRST_ACCEPTANCE_THEN_FRESH_ASIA_REACCEPTANCE"
            for event in engine.events
        ))

    def test_target_consumed_before_decision_is_rejected(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_london(engine)
        engine._on_five(bar(ts(y, m, d, 12, 5), 104, 108, 103, 107), True)
        engine._on_five(bar(ts(y, m, d, 12, 10), 107, 109, 96, 98), True)
        plan = engine._on_five(bar(ts(y, m, d, 12, 15), 100, 103, 98, 102), True)
        self.assertIsNone(plan)
        self.assertEqual(engine.skips["STRUCTURAL_TARGET_REACHED_BEFORE_DECISION"], 1)

    def test_weekend_does_not_arm_episode(self) -> None:
        y, m, d = (2024, 5, 18)  # Saturday
        engine = CausalLiquidityAuctionEngine(config(), "X")
        engine._on_five(bar(ts(y, m, d, 0, 5), 100, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 0, 10), 100, 102, 98, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 0), 100, 105, 95, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 5), 104, 108, 103, 104), True)
        self.assertFalse(engine.scenario_counts)

    def test_event_chronology_is_causal(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 107, 108, 99.5, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 103, 103.5, 99.5, 100), True)
        self.assertTrue(engine.events)
        self.assertTrue(all(event.observed_time_ns >= event.event_time_ns for event in engine.events))


if __name__ == "__main__":
    unittest.main()
