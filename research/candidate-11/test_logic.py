from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logic import (
    Auction,
    BarObs,
    CausalAuctionEngine,
    Direction,
    LogicConfig,
    Pool,
    RiskSizer,
    Scenario,
    Side,
    StructuralBar,
    MINUTE_NS,
)
from session_engine import RegionalHandoffAuctionEngine, SESSION_SPECS


def bar(ts: int, open_: float, high: float, low: float, close: float, volume: float = 100.0, buy: float = 50.0) -> BarObs:
    return BarObs(ts, open_, high, low, close, volume, buy)


def pool(
    scenario_id: str,
    side: Side,
    level: float,
    *,
    range_id: str | None = None,
    opposite: float | None = None,
    strength: int = 3,
    close_location: float = 0.5,
    signed_flow: float = 0.0,
) -> Pool:
    return Pool(
        scenario_id=scenario_id,
        side=side,
        level=level,
        source="TEST_RANGE",
        candidate_ts_ns=1,
        confirmed_ts_ns=2,
        confirmed_index=0,
        expiry_index=10_000,
        range_id=range_id,
        opposite_level=opposite,
        strength=strength,
        range_close_location=close_location,
        range_signed_flow=signed_flow,
    )


class TestRiskContract(unittest.TestCase):
    def test_exact_nav_risk_is_floored_not_scaled_by_score(self) -> None:
        decision = RiskSizer(0.03).size(
            nav=Decimal("100000"),
            loss_per_unit=Decimal("100"),
            entry_price=Decimal("60000"),
            quantity_increment=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("10"),
            margin_init=Decimal("0.05"),
            free_balance=Decimal("100000"),
        )
        self.assertTrue(decision.feasible)
        self.assertEqual(decision.planned_loss_budget, Decimal("3000.00"))
        self.assertEqual(decision.quantity, Decimal("30.00"))
        self.assertLessEqual(decision.expected_total_loss, decision.planned_loss_budget)

    def test_actual_margin_infeasibility_rejects_instead_of_clipping(self) -> None:
        decision = RiskSizer(0.03).size(
            nav=Decimal("1000"),
            loss_per_unit=Decimal("1"),
            entry_price=Decimal("1000"),
            quantity_increment=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("10"),
            margin_init=Decimal("0.50"),
            free_balance=Decimal("1000"),
        )
        self.assertFalse(decision.feasible)
        self.assertEqual(decision.reason, "ACTUAL_MARGIN_INFEASIBLE")
        self.assertEqual(decision.quantity, Decimal("0"))


class TestCausalPlanContract(unittest.TestCase):
    def setUp(self) -> None:
        self.config = LogicConfig(min_net_r=1.25, min_stop_atr=0.08, retrace_expiry_bars=12)
        self.engine = CausalAuctionEngine(self.config, "BTCUSDT-PERP.BINANCE")

    def _auction(self, direction: Direction) -> tuple[Auction, BarObs]:
        source = pool("P-HIGH", Side.HIGH, 100.0, range_id="R", opposite=80.0)
        confirmation = bar(100 * MINUTE_NS, 106.0, 112.0, 104.0, 110.0, buy=70.0)
        auction = Auction(
            pool=source,
            sweep=bar(90 * MINUTE_NS, 99.0, 104.0, 98.0, 101.0, buy=70.0),
            sweep_index=0,
            atr=10.0,
            internal_level=98.0,
            sweep_extreme=104.0,
            rejection_seed=True,
            acceptance_seed=True,
        )
        auction.scenario = Scenario.FAR
        auction.direction = direction
        auction.state = "FAR_CONFIRMED"
        if direction == Direction.LONG:
            auction.zone_low, auction.zone_high = 100.0, 105.0
            auction.stop_price, auction.target_price = 90.0, 135.0
        else:
            auction.zone_low, auction.zone_high = 105.0, 110.0
            auction.stop_price, auction.target_price = 120.0, 75.0
            confirmation = bar(100 * MINUTE_NS, 104.0, 106.0, 98.0, 100.0, buy=30.0)
        self.engine.active = auction
        return auction, confirmation

    def test_long_plan_is_passive_gtd_limit_at_void_edge(self) -> None:
        auction, confirmation = self._auction(Direction.LONG)
        plan = self.engine._costed_limit_plan(auction, confirmation, "TEST")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.expected_entry, 105.0)
        self.assertEqual(plan.entry_order_type, "LIMIT")
        self.assertTrue(plan.entry_post_only)
        self.assertLess(plan.expected_entry, confirmation.close)
        self.assertEqual(plan.expire_ts_ns, confirmation.ts_ns + 12 * MINUTE_NS)
        self.assertEqual(plan.details["entry_cost_assumption"], "MAKER")
        self.assertGreaterEqual(plan.net_r, self.config.min_net_r)

    def test_short_plan_is_passive_at_low_edge(self) -> None:
        auction, confirmation = self._auction(Direction.SHORT)
        plan = self.engine._costed_limit_plan(auction, confirmation, "TEST")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.expected_entry, 105.0)
        self.assertGreater(plan.expected_entry, confirmation.close)

    def test_plan_occupies_global_slot_until_nautilus_terminal_event(self) -> None:
        auction, confirmation = self._auction(Direction.LONG)
        plan = self.engine._costed_limit_plan(auction, confirmation, "TEST")
        assert plan is not None
        self.engine.mark_submitted(plan, Decimal("1"))
        self.assertEqual(self.engine.active_trade_state, "PENDING_ENTRY")
        self.engine.mark_entry_filled(confirmation.ts_ns + MINUTE_NS)
        self.assertEqual(self.engine.active_trade_state, "POSITION")
        self.engine.mark_trade_terminal(confirmation.ts_ns + 2 * MINUTE_NS, "TEST_EXIT")
        self.assertIsNone(self.engine.active_trade_id)

    def test_far_confirmation_plan_and_rejection_form_one_state_chain(self) -> None:
        trigger = pool("TRIGGER", Side.HIGH, 100.0, range_id="R", opposite=80.0)
        target = pool("TARGET", Side.LOW, 80.0, range_id="R", opposite=100.0)
        self.engine.pools = [trigger, target]
        confirmation = bar(100 * MINUTE_NS, 104.0, 106.0, 98.0, 100.0, buy=20.0)
        auction = Auction(
            pool=trigger,
            sweep=bar(90 * MINUTE_NS, 99.0, 104.0, 98.0, 101.0, buy=70.0),
            sweep_index=0,
            atr=10.0,
            internal_level=103.0,
            sweep_extreme=104.0,
            rejection_seed=True,
            acceptance_seed=False,
            reclaim_seen=True,
            reversal_target_pool_id=target.scenario_id,
            reversal_target_level=target.level,
        )
        self.engine.active = auction
        self.engine.bars = [confirmation]
        self.engine._index = 0
        plan = self.engine._confirm_far(auction, confirmation)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.engine.mark_rejected(plan, confirmation.ts_ns, "TEST_REJECTION")
        last_by_scenario = {}
        for event in self.engine.events:
            previous = last_by_scenario.get(event.scenario_id)
            if previous is not None:
                self.assertEqual(event.previous_state, previous.next_state)
            last_by_scenario[event.scenario_id] = event
        self.assertEqual(
            [(event.previous_state, event.next_state) for event in self.engine.events],
            [("OBSERVE", "FAR_CONFIRMED"), ("FAR_CONFIRMED", "PENDING_ENTRY"), ("PENDING_ENTRY", "TERMINAL")],
        )

    def test_insufficient_costed_r_is_terminal_not_tuned(self) -> None:
        auction, confirmation = self._auction(Direction.LONG)
        auction.target_price = 108.0
        plan = self.engine._costed_limit_plan(auction, confirmation, "TEST")
        self.assertIsNone(plan)
        self.assertIsNone(self.engine.active)
        self.assertEqual(self.engine.skips["INSUFFICIENT_COSTED_STRUCTURAL_R"], 1)


class TestDrawAndSessionSemantics(unittest.TestCase):
    def test_source_range_alignment_is_side_relative(self) -> None:
        engine = CausalAuctionEngine(LogicConfig(), "TEST")
        high = pool("H", Side.HIGH, 110.0, close_location=0.80, signed_flow=0.10)
        low = pool("L", Side.LOW, 90.0, close_location=0.80, signed_flow=0.10)
        self.assertGreater(engine._source_range_side_alignment(high), 0)
        self.assertLess(engine._source_range_side_alignment(low), 0)

    def test_far_target_ignores_weaker_internal_obstacle(self) -> None:
        engine = CausalAuctionEngine(LogicConfig(), "TEST")
        engine._index = 10
        trigger = pool("TRIGGER", Side.HIGH, 110.0, range_id="R", opposite=80.0, strength=3)
        paired = pool("PAIRED", Side.LOW, 80.0, range_id="R", opposite=110.0, strength=3)
        weak = pool("WEAK", Side.LOW, 90.0, strength=2)
        engine.pools = [trigger, paired, weak]
        self.assertIs(engine._far_target_pool(trigger, 105.0), paired)
        strong = pool("STRONG", Side.LOW, 90.0, strength=3)
        engine.pools.append(strong)
        self.assertIs(engine._far_target_pool(trigger, 105.0), strong)

    def test_completed_cross_midnight_session_has_future_decision_window(self) -> None:
        engine = RegionalHandoffAuctionEngine(LogicConfig(), "TEST")
        spec = next(item for item in SESSION_SPECS if item.label == "ASIA_2000_0000_NY")
        key = date(2024, 8, 19)
        structural = StructuralBar(
            start_ts_ns=1,
            end_ts_ns=2,
            open=100.0,
            high=110.0,
            low=90.0,
            close=108.0,
            volume=1000.0,
            taker_buy_volume=650.0,
            high_ts_ns=1,
            low_ts_ns=1,
        )
        engine._sessions[(spec.label, key)] = structural
        engine._finish_session(spec, key)
        created = [item for item in engine.pools if item.source == spec.label]
        self.assertEqual(len(created), 2)
        expected_start = engine._minute_ns(key + __import__("datetime").timedelta(days=1), 2 * 60)
        expected_end = engine._minute_ns(key + __import__("datetime").timedelta(days=1), 5 * 60)
        self.assertTrue(all(item.triggerable for item in created))
        self.assertTrue(all(item.trigger_start_ts_ns == expected_start for item in created))
        self.assertTrue(all(item.trigger_end_ts_ns == expected_end for item in created))


class TestProductionBoundary(unittest.TestCase):
    def test_runner_delegates_execution_to_nautilus(self) -> None:
        source = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn("entry_order_type=OrderType.LIMIT", source)
        self.assertIn("time_in_force=TimeInForce.GTD", source)
        self.assertIn("engine.trader.generate_order_fills_report()", source)
        self.assertNotIn("def simulate_fill", source)
        self.assertNotIn("def backtest_loop", source)

    def test_gtd_expiry_uses_timezone_aware_datetime(self) -> None:
        source = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn("expire_time=datetime.fromtimestamp(", source)
        self.assertIn("tz=timezone.utc", source)
        self.assertIn("+ timedelta(microseconds=1)", source)
        self.assertNotIn("expire_time=plan.expire_ts_ns", source)


class TestCausalEpisodeMemory(unittest.TestCase):
    def test_sweep_causality_expires_with_internal_structure_memory(self) -> None:
        config = LogicConfig()
        self.assertEqual(config.causal_episode_bars, config.internal_tf_bars * config.internal_lookback)
        engine = CausalAuctionEngine(config, "TEST")
        previous = bar(1, 100.0, 101.0, 99.0, 100.0)
        trigger = pool("EPISODE", Side.HIGH, 110.0, range_id="R", opposite=90.0)
        trigger.trigger_start_ts_ns = 0
        trigger.trigger_end_ts_ns = 1 << 62
        engine.bars = [previous]
        engine.true_ranges.extend([1.0] * config.atr_period)
        engine.volumes.extend([100.0] * config.volume_period)
        engine._index = 0
        engine.active = Auction(
            pool=trigger,
            sweep=bar(2, 109.0, 111.0, 108.0, 110.5),
            sweep_index=0,
            atr=1.0,
            internal_level=105.0,
            sweep_extreme=111.0,
            rejection_seed=False,
            acceptance_seed=False,
            elapsed=config.causal_episode_bars,
        )
        plan = engine.on_bar(bar(MINUTE_NS, 100.0, 101.0, 99.0, 100.0))
        self.assertIsNone(plan)
        self.assertIsNone(engine.active)
        self.assertEqual(engine.skips["CAUSAL_EPISODE_MEMORY_EXPIRED"], 1)
        self.assertEqual(engine.events[-1].reason_code, "CAUSAL_EPISODE_MEMORY_EXPIRED")


if __name__ == "__main__":
    unittest.main()
