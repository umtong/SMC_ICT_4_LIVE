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
        "delayed_rejection_fvg_body_atr": 0.2,
        "low_acceptance_displacement_body_atr": 0.2,
        "low_acceptance_pullback_body_atr": 0.1,
        "low_acceptance_fvg_min_atr": 0.05,
        "low_reacceptance_body_atr": 0.2,
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
        # The completed close is below the intended buy limit, so the order
        # is immediately marketable rather than waiting for more selling.
        plan = engine._on_five(bar(decision_ts, 108, 108.2, 105.2, 105.4), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.entry_order, EntryOrder.LIMIT_GTD)
        self.assertEqual(plan.expire_ts_ns, decision_ts + 5 * NS_MINUTE)
        self.assertAlmostEqual(plan.expected_entry, 105.5)

    def test_passive_mitigation_cannot_rest_below_completed_close(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        self.form_asia_high_acceptance(engine)
        # The bearish mitigation holds the FVG lower edge, but its completed
        # close remains above the proposed limit.  Filling would therefore
        # require continuing sell pressure and contradict passive absorption.
        plan = engine._on_five(
            bar(ts(y, m, d, 6, 20), 108, 108.2, 105.2, 105.7),
            True,
        )
        self.assertIsNone(plan)
        self.assertEqual(
            engine.skips["PASSIVE_LIMIT_NOT_MARKETABLE_AFTER_RETEST"],
            1,
        )
        state = engine._sources[SessionLabel.ASIA]
        self.assertEqual(state.acceptance_phase, "MONITOR_FAILURE")
        self.assertTrue(
            any(
                event.reason_code
                == "PROTECTED_BUY_LIMIT_WOULD_REST_INTO_CONTINUING_SELL_PRESSURE"
                for event in engine.events
            )
        )

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
        engine._on_five(bar(ts(y, m, d, 6, 25), 105.2, 105.4, 104, 104.5), True)
        engine._on_five(bar(ts(y, m, d, 6, 30), 104.5, 109, 104.4, 108.5), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 35), 108.5, 110, 106.5, 109), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.ASIA_HIGH_REACCEPTANCE)
        self.assertEqual(plan.entry_order, EntryOrder.MARKET)



    def test_reacceptance_requires_fresh_fvg_to_overlap_preserved_imbalance(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        self.form_asia_high_acceptance(engine)
        self.assertIsNone(
            engine._on_five(bar(ts(y, m, d, 6, 20), 106, 107, 105.2, 106.1), True)
        )
        # The close back inside preserves the original 104.0-105.5 FVG, but
        # the later fresh FVG starts above it at 106.2.  That is a disconnected
        # extension, not a repair of the failed acceptance.
        engine._on_five(bar(ts(y, m, d, 6, 25), 106.1, 106.2, 104, 104.5), True)
        engine._on_five(bar(ts(y, m, d, 6, 30), 104.5, 109, 104.4, 108.5), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 35), 108.5, 110, 106.5, 109), True)
        self.assertIsNone(plan)
        self.assertEqual(
            engine.skips[
                "ASIA_REACCEPTANCE_FVG_DISCONNECTED_FROM_PRESERVED_IMBALANCE"
            ],
            1,
        )
        state = engine._sources[SessionLabel.ASIA]
        self.assertFalse(state.failed_high_acceptance)
        self.assertTrue(state.reacceptance_done)
        self.assertIsNone(state.reacceptance_anchor_fvg)
        self.assertTrue(
            any(
                event.reason_code
                == "FIRST_FRESH_FVG_DID_NOT_REPAIR_PRESERVED_ORIGINAL_IMBALANCE"
                for event in engine.events
            )
        )

    def test_deep_acceptance_failure_cannot_reuse_destroyed_fvg(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        self.form_asia_high_acceptance(engine)
        # A non-marketable passive mitigation is rejected first.
        self.assertIsNone(
            engine._on_five(
                bar(ts(y, m, d, 6, 20), 108, 108.2, 105.2, 105.7),
                True,
            )
        )
        # Closing back inside below the original FVG lower edge destroys the
        # imbalance which justified the first acceptance.
        engine._on_five(bar(ts(y, m, d, 6, 25), 105.7, 106, 103.5, 103.8), True)
        state = engine._sources[SessionLabel.ASIA]
        self.assertFalse(state.failed_high_acceptance)
        self.assertTrue(state.reacceptance_done)
        self.assertEqual(
            engine.skips["HIGH_ACCEPTANCE_ORIGINAL_FVG_INVALIDATED"],
            1,
        )
        # Even a later fresh bullish FVG cannot reuse the destroyed acceptance
        # and its full-range target as a re-acceptance trade.
        engine._on_five(bar(ts(y, m, d, 6, 30), 103.8, 109, 103.7, 108.5), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 35), 108.5, 110, 106.5, 109), True)
        self.assertIsNone(plan)

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

    def test_opposite_boundaries_have_independent_causal_lifecycles(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 107, 108, 99.5, 100), True)
        first = engine._on_five(bar(ts(y, m, d, 6, 10), 103, 103.5, 99.5, 100), True)
        self.assertIsNotNone(first)
        state = engine._sources[SessionLabel.ASIA]
        self.assertTrue(state.high_plan_emitted)
        self.assertFalse(state.low_plan_emitted)
        # Buy-side and sell-side liquidity are distinct causal boundaries.
        self.assertIsNone(
            engine._on_five(bar(ts(y, m, d, 6, 15), 89, 98, 88, 97), True)
        )
        second = engine._on_five(bar(ts(y, m, d, 6, 20), 96, 100, 95, 99), True)
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.scenario, ScenarioKind.ASIA_LOW_REJECTION)
        self.assertTrue(state.low_plan_emitted)

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

    def test_delayed_asia_high_rejection_uses_one_bar_fvg_limit(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        # Raid and strong reclaim, but the immediate next bar is not displacement.
        engine._on_five(bar(ts(y, m, d, 6, 5), 107, 108, 101, 104), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 104, 104.5, 102.5, 103.8), True)
        # Later bearish displacement creates a causal FVG while the raid extreme holds.
        engine._on_five(bar(ts(y, m, d, 6, 15), 103.8, 104, 102, 103), True)
        engine._on_five(bar(ts(y, m, d, 6, 20), 103, 103.2, 98, 99), True)
        decision_ts = ts(y, m, d, 6, 25)
        plan = engine._on_five(bar(decision_ts, 99.8, 101, 99.2, 99.5), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.ASIA_HIGH_DELAYED_REJECTION)
        self.assertEqual(plan.entry_order, EntryOrder.LIMIT_GTD)
        self.assertEqual(plan.expire_ts_ns, decision_ts + 5 * NS_MINUTE)

    def test_low_acceptance_requires_failed_pullback_near_completed_boundary(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 99, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 100, 100, 91, 92), True)
        engine._on_five(bar(ts(y, m, d, 6, 15), 92, 96.1, 90, 93), True)
        decision_ts = ts(y, m, d, 6, 20)
        plan = engine._on_five(bar(decision_ts, 93, 95.2, 92.8, 94.8), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.ASIA_LOW_ACCEPTANCE)
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.entry_order, EntryOrder.LIMIT_GTD)
        self.assertEqual(plan.expire_ts_ns, decision_ts + 5 * NS_MINUTE)

    def test_low_acceptance_does_not_chase_far_below_boundary(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(
            config(low_acceptance_max_entry_distance_atr=0.1),
            "X",
        )
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 99, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 100, 100, 87, 88), True)
        engine._on_five(bar(ts(y, m, d, 6, 15), 88, 91, 86, 87), True)
        self.assertIsNone(
            engine._on_five(bar(ts(y, m, d, 6, 20), 87, 90, 86.5, 89.5), True)
        )
        self.assertEqual(engine.skips["LOW_ACCEPTANCE_PULLBACK_NOT_STRUCTURAL"], 1)

    def test_distant_low_acceptance_waits_for_local_reacceleration(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(
            config(low_acceptance_max_entry_distance_atr=0.1),
            "X",
        )
        self.seed_asia(engine)
        # Initial sell-side acceptance forms far below the completed boundary.
        engine._on_five(bar(ts(y, m, d, 6, 5), 99, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 100, 100, 87, 88), True)
        engine._on_five(bar(ts(y, m, d, 6, 15), 88, 91, 86, 87), True)
        # A bullish pullback is too distant for the completed-session entry.
        self.assertIsNone(
            engine._on_five(bar(ts(y, m, d, 6, 20), 87, 90, 86.5, 89.5), True)
        )
        state = engine._sources[SessionLabel.ASIA]
        self.assertEqual(state.low_acceptance_phase, "WAIT_LOCAL_REACCELERATION")
        # The local pullback establishes its own invalidation.  A fresh bearish
        # FVG then confirms reacceleration while the completed low remains accepted.
        engine._on_five(bar(ts(y, m, d, 6, 25), 89.5, 92, 89, 91), True)
        engine._on_five(bar(ts(y, m, d, 6, 30), 91, 91, 87, 87.5), True)
        plan = engine._on_five(
            bar(ts(y, m, d, 6, 35), 87.5, 88.5, 86.8, 87.2),
            True,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.ASIA_LOW_ACCEPTANCE_REACCELERATION)
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.entry_order, EntryOrder.LIMIT_GTD)
        self.assertGreater(plan.stop_price, 92.0)
        self.assertEqual(plan.target_price, 86.0)
        self.assertEqual(
            plan.details["target_semantics"],
            "PRIOR_ACCEPTANCE_EXPANSION_LOW",
        )

    def test_local_low_reacceleration_dies_when_session_low_is_reclaimed(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(
            config(low_acceptance_max_entry_distance_atr=0.1),
            "X",
        )
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 99, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 100, 100, 87, 88), True)
        engine._on_five(bar(ts(y, m, d, 6, 15), 88, 91, 86, 87), True)
        engine._on_five(bar(ts(y, m, d, 6, 20), 87, 90, 86.5, 89.5), True)
        state = engine._sources[SessionLabel.ASIA]
        self.assertEqual(state.low_acceptance_phase, "WAIT_LOCAL_REACCELERATION")
        # Closing back inside the completed range invalidates the continuation
        # context before any later local FVG can be used.
        engine._on_five(bar(ts(y, m, d, 6, 25), 89.5, 97, 89, 96), True)
        self.assertEqual(state.low_acceptance_phase, "WAIT_REACCEPT")
        engine._on_five(bar(ts(y, m, d, 6, 30), 96, 96, 87, 88), True)
        plan = engine._on_five(
            bar(ts(y, m, d, 6, 35), 88, 89, 86.8, 87.2),
            True,
        )
        self.assertIsNone(plan)
        self.assertFalse(any(
            event.details.get("route")
            == "DISTANT_LOW_ACCEPTANCE_PULLBACK_THEN_LOCAL_BEARISH_REACCELERATION"
            for event in engine.events
        ))

    def test_failed_premium_low_acceptance_can_reaccept_bearishly(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(), "X")
        # Complete Asia at premium: high 105, low 95, close 103.
        engine._on_five(bar(ts(y, m, d, 0, 5), 100, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 0, 10), 100, 102, 98, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 0), 100, 105, 95, 103), True)
        engine._on_five(bar(ts(y, m, d, 6, 5), 99, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 100, 100, 91, 92), True)
        engine._on_five(bar(ts(y, m, d, 6, 15), 92, 96.1, 90, 93), True)
        # Initial downside acceptance fails back inside.
        engine._on_five(bar(ts(y, m, d, 6, 20), 93, 97, 92, 96), True)
        engine._on_five(bar(ts(y, m, d, 6, 25), 96, 96, 93, 94), True)
        plan = engine._on_five(bar(ts(y, m, d, 6, 30), 94, 95, 89.5, 90), True)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.ASIA_LOW_REACCEPTANCE)
        self.assertEqual(plan.entry_order, EntryOrder.MARKET)

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
