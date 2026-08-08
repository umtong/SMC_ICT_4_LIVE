from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
import unittest

import candidate32_effort_result_absorption as module
from candidate32_effort_result_absorption import (
    EffortResultState,
    SCENARIO_KIND,
    _market_economics,
    _select_first_test,
    _step,
)
from logic import BarObs, CausalAuctionEngine, Direction, MINUTE_NS, Scenario, Side


class Candidate32EffortResultAbsorptionTests(unittest.TestCase):
    def engine(self):
        events: list[tuple[object, ...]] = []
        target = SimpleNamespace(
            scenario_id="TARGET-LOW",
            level=90.0,
            source="PREVIOUS_UTC_DAY",
            consumed=False,
            expiry_index=500,
        )
        engine = SimpleNamespace(
            config=SimpleNamespace(
                event_expiry_bars=60,
                acceptance_close_atr=0.08,
                rejection_reclaim_atr=0.05,
                acceptance_min_closes=2,
                absorption_flow_min=0.08,
                min_relative_volume=0.85,
                sweep_min_atr=0.05,
                sweep_max_atr=2.50,
                displacement_flow_min=0.03,
                acceptance_close_location=0.60,
                displacement_body_atr=0.20,
                stop_buffer_atr=0.08,
                effective_taker_rate=0.0008,
                effective_maker_rate=0.0004,
                min_stop_atr=0.08,
                min_net_r=1.25,
            ),
            pools=[target],
            median_volume=100.0,
            _index=10,
            skips=Counter(),
            _candidate32_effort_result_state=None,
            _event=lambda *args: events.append(args),
        )
        return engine, events

    def state(self) -> EffortResultState:
        return EffortResultState(
            scenario_id="BTC-ERA-1-2-SHORT",
            swept_side=Side.HIGH,
            direction=Direction.SHORT,
            pivot_candidate_ts_ns=1 * MINUTE_NS,
            pivot_known_ts_ns=2 * MINUTE_NS,
            pivot_level=100.0,
            target_pool_id="TARGET-LOW",
            target_price=90.0,
            target_source="PREVIOUS_UTC_DAY",
            first_test_ts_ns=10 * MINUTE_NS,
            first_test_index=10,
            first_penetration_atr=1.0,
            first_abs_flow=0.80,
            first_extreme=101.0,
            expiry_index=100,
        )

    def test_known_pivot_selection_rejects_future_knowledge(self) -> None:
        engine = SimpleNamespace(
            config=SimpleNamespace(
                event_expiry_bars=60,
                absorption_flow_min=0.08,
                sweep_min_atr=0.05,
                sweep_max_atr=2.50,
            ),
            internal_highs=[
                (1 * MINUTE_NS, 2 * MINUTE_NS, 100.0),
                (8 * MINUTE_NS, 12 * MINUTE_NS, 101.0),
            ],
            internal_lows=[],
            _candidate32_consumed_internal_keys=set(),
        )
        prev = BarObs(9 * MINUTE_NS, 99.5, 99.8, 99.2, 99.7, 100.0, 50.0)
        bar = BarObs(10 * MINUTE_NS, 99.7, 100.6, 99.6, 100.4, 100.0, 90.0)
        selected = _select_first_test(engine, bar, prev, 1.0)
        self.assertIsNotNone(selected)
        self.assertEqual(selected[3], 100.0)
        self.assertLess(selected[2], bar.ts_ns)

    def test_more_effort_without_more_result_then_reclaim_builds_short_far(self) -> None:
        engine, events = self.engine()
        state = self.state()
        engine._candidate32_effort_result_state = state

        engine._index = 11
        prev = BarObs(10 * MINUTE_NS, 100.0, 101.0, 99.8, 100.5, 100.0, 90.0)
        reclaim1 = BarObs(11 * MINUTE_NS, 100.4, 100.5, 99.2, 99.4, 100.0, 20.0)
        self.assertIsNone(_step(engine, state, reclaim1, prev, 1.0))
        self.assertEqual(state.state, "WAIT_SECOND_TEST")

        engine._index = 12
        second = BarObs(12 * MINUTE_NS, 99.4, 100.5, 99.3, 100.3, 120.0, 114.0)
        self.assertIsNone(_step(engine, state, second, reclaim1, 1.0))
        self.assertEqual(state.state, "WAIT_FINAL_RECLAIM")
        self.assertAlmostEqual(state.second_penetration_atr, 0.5)
        self.assertGreaterEqual(state.second_abs_flow, state.first_abs_flow)

        # The second-test bar cannot also be its own final reclaim evidence.
        self.assertIsNone(_step(engine, state, second, reclaim1, 1.0))

        engine._index = 13
        final = BarObs(13 * MINUTE_NS, 100.3, 100.4, 98.8, 99.0, 120.0, 12.0)
        plan = _step(engine, state, final, second, 1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.scenario, Scenario.FAR)
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.entry_order_type, "MARKET")
        self.assertFalse(plan.entry_post_only)
        self.assertEqual(plan.details["scenario_kind"], SCENARIO_KIND)
        self.assertEqual(plan.target_price, 90.0)
        self.assertGreater(plan.stop_price, 101.0)
        self.assertGreater(plan.net_r, 1.25)
        self.assertEqual(events[-1][1], "TRADE_PLAN_CONFIRMED")

    def test_greater_second_price_result_resets_benchmark(self) -> None:
        engine, events = self.engine()
        state = self.state()
        state.state = "WAIT_SECOND_TEST"
        engine._candidate32_effort_result_state = state
        engine._index = 12
        prev = BarObs(11 * MINUTE_NS, 99.7, 99.9, 99.2, 99.6, 100.0, 20.0)
        deeper = BarObs(12 * MINUTE_NS, 99.6, 101.4, 99.5, 101.1, 120.0, 114.0)
        self.assertIsNone(_step(engine, state, deeper, prev, 1.0))
        self.assertEqual(state.state, "WAIT_FIRST_RECLAIM")
        self.assertAlmostEqual(state.first_penetration_atr, 1.4)
        self.assertEqual(state.first_test_index, 12)
        self.assertEqual(events[-1][1], "EFFORT_RESULT_BENCHMARK_ADVANCED")

    def test_cost_model_reserves_two_taker_legs_and_maker_target(self) -> None:
        risk, loss, gain, net_r = _market_economics(
            direction=Direction.SHORT,
            entry=99.0,
            stop=101.0,
            target=90.0,
            taker_rate=0.0008,
            maker_rate=0.0004,
        )
        self.assertAlmostEqual(risk, 2.0)
        self.assertAlmostEqual(loss, 2.1600)
        self.assertAlmostEqual(gain, 8.8848)
        self.assertAlmostEqual(net_r, 8.8848 / 2.1600)

    def test_install_captures_existing_lifecycle_hooks(self) -> None:
        old_methods = (
            CausalAuctionEngine.on_bar,
            CausalAuctionEngine.mark_submitted,
            CausalAuctionEngine.mark_rejected,
            CausalAuctionEngine.mark_trade_terminal,
        )
        old_bases = (
            module.BASE_ON_BAR,
            module.BASE_MARK_SUBMITTED,
            module.BASE_MARK_REJECTED,
            module.BASE_MARK_TRADE_TERMINAL,
        )

        def prior_on_bar(*args, **kwargs):
            return None

        def prior_submitted(*args, **kwargs):
            return None

        def prior_rejected(*args, **kwargs):
            return None

        def prior_terminal(*args, **kwargs):
            return None

        try:
            CausalAuctionEngine.on_bar = prior_on_bar
            CausalAuctionEngine.mark_submitted = prior_submitted
            CausalAuctionEngine.mark_rejected = prior_rejected
            CausalAuctionEngine.mark_trade_terminal = prior_terminal
            module.BASE_ON_BAR = None
            module.BASE_MARK_SUBMITTED = None
            module.BASE_MARK_REJECTED = None
            module.BASE_MARK_TRADE_TERMINAL = None
            module.install()
            self.assertIs(module.BASE_ON_BAR, prior_on_bar)
            self.assertIs(module.BASE_MARK_SUBMITTED, prior_submitted)
            self.assertIs(module.BASE_MARK_REJECTED, prior_rejected)
            self.assertIs(module.BASE_MARK_TRADE_TERMINAL, prior_terminal)
            self.assertIs(CausalAuctionEngine.on_bar, module.candidate32_on_bar)
        finally:
            (
                CausalAuctionEngine.on_bar,
                CausalAuctionEngine.mark_submitted,
                CausalAuctionEngine.mark_rejected,
                CausalAuctionEngine.mark_trade_terminal,
            ) = old_methods
            (
                module.BASE_ON_BAR,
                module.BASE_MARK_SUBMITTED,
                module.BASE_MARK_REJECTED,
                module.BASE_MARK_TRADE_TERMINAL,
            ) = old_bases


if __name__ == "__main__":
    unittest.main(verbosity=2)
