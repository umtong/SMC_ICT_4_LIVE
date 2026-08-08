from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
import unittest

import candidate29_quarter_hour_failed_auction as detector
import candidate30_quarter_hour_boundary_retest as module
from candidate30_quarter_hour_boundary_retest import (
    SCENARIO_KIND,
    _passive_economics,
    _step,
)
from logic import BarObs, CausalAuctionEngine, Direction, MINUTE_NS, Scenario, Side


class Candidate30QuarterHourBoundaryRetestTests(unittest.TestCase):
    def state(self, *, direction: Direction, swept_side: Side):
        return detector.QuarterHourFailedAuctionState(
            scenario_id=f"BTC-QHF-1-2-{direction.value}",
            swept_side=swept_side,
            direction=direction,
            boundary_ts_ns=15 * MINUTE_NS,
            opening_ts_ns=16 * MINUTE_NS,
            opening_index=20,
            expiry_index=32,
            range_start_ts_ns=1 * MINUTE_NS,
            range_end_ts_ns=15 * MINUTE_NS,
            range_high=105.0,
            range_low=93.0,
            range_midpoint=99.0,
            sweep_extreme=106.0 if direction == Direction.SHORT else 92.0,
            episode_break_level=104.8 if direction == Direction.SHORT else 93.2,
            opening_signed_flow=0.8 if direction == Direction.SHORT else -0.8,
            opening_relative_volume=2.0,
        )

    def engine(self):
        events: list[tuple[object, ...]] = []
        engine = SimpleNamespace(
            config=SimpleNamespace(
                acceptance_close_atr=0.08,
                rejection_reclaim_atr=0.05,
                acceptance_min_closes=2,
                displacement_flow_min=0.03,
                acceptance_close_location=0.60,
                displacement_body_atr=0.20,
                stop_buffer_atr=0.08,
                effective_taker_rate=0.0008,
                effective_maker_rate=0.0004,
                min_stop_atr=0.08,
                min_net_r=1.25,
            ),
            _index=21,
            skips=Counter(),
            _candidate29_quarter_hour_failed_state=None,
            _event=lambda *args: events.append(args),
        )
        return engine, events

    def test_reclaim_bar_builds_passive_short_boundary_retest(self) -> None:
        engine, events = self.engine()
        state = self.state(direction=Direction.SHORT, swept_side=Side.HIGH)
        engine._candidate29_quarter_hour_failed_state = state
        reclaim = BarObs(
            17 * MINUTE_NS,
            105.4,
            105.5,
            104.4,
            104.6,
            100.0,
            20.0,
        )
        plan = _step(engine, state, reclaim, 1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.scenario, Scenario.FAR)
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.entry_order_type, "LIMIT")
        self.assertTrue(plan.entry_post_only)
        self.assertEqual(plan.expected_entry, 105.0)
        self.assertEqual(plan.target_price, 99.0)
        self.assertGreater(plan.stop_price, 106.0)
        self.assertGreater(plan.net_r, 1.25)
        self.assertEqual(plan.details["scenario_kind"], SCENARIO_KIND)
        self.assertEqual(
            plan.expire_ts_ns,
            state.boundary_ts_ns + 15 * MINUTE_NS,
        )
        self.assertEqual(events[-1][1], "TRADE_PLAN_CONFIRMED")

    def test_opening_bar_cannot_confirm_its_own_reclaim(self) -> None:
        engine, _ = self.engine()
        engine._index = 20
        state = self.state(direction=Direction.SHORT, swept_side=Side.HIGH)
        reclaim = BarObs(
            16 * MINUTE_NS,
            105.4,
            105.5,
            104.4,
            104.6,
            100.0,
            20.0,
        )
        self.assertIsNone(_step(engine, state, reclaim, 1.0))
        self.assertEqual(state.state, "WAIT_RECLAIM")

    def test_long_contract_is_directionally_symmetric(self) -> None:
        engine, _ = self.engine()
        state = self.state(direction=Direction.LONG, swept_side=Side.LOW)
        state.range_midpoint = 99.0
        reclaim = BarObs(
            17 * MINUTE_NS,
            92.8,
            93.8,
            92.7,
            93.6,
            100.0,
            80.0,
        )
        plan = _step(engine, state, reclaim, 1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertEqual(plan.expected_entry, 93.0)
        self.assertLess(plan.stop_price, 92.0)
        self.assertEqual(plan.target_price, 99.0)

    def test_passive_costs_reserve_maker_entry_taker_stop_and_maker_target(self) -> None:
        risk, loss, gain, net_r = _passive_economics(
            direction=Direction.SHORT,
            entry=105.0,
            stop=106.0,
            target=99.0,
            maker_rate=0.0004,
            taker_rate=0.0008,
        )
        self.assertAlmostEqual(risk, 1.0)
        self.assertAlmostEqual(loss, 1.1268)
        self.assertAlmostEqual(gain, 5.9184)
        self.assertAlmostEqual(net_r, 5.9184 / 1.1268)

    def test_install_replaces_only_candidate29_transition(self) -> None:
        old_methods = (
            CausalAuctionEngine.on_bar,
            CausalAuctionEngine.mark_submitted,
            CausalAuctionEngine.mark_rejected,
            CausalAuctionEngine.mark_trade_terminal,
        )
        old_step = detector._step
        old_kind = detector.SCENARIO_KIND
        old_bases = (
            detector.BASE_ON_BAR,
            detector.BASE_MARK_SUBMITTED,
            detector.BASE_MARK_REJECTED,
            detector.BASE_MARK_TRADE_TERMINAL,
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
            detector.BASE_ON_BAR = None
            detector.BASE_MARK_SUBMITTED = None
            detector.BASE_MARK_REJECTED = None
            detector.BASE_MARK_TRADE_TERMINAL = None
            module.install()
            self.assertIs(CausalAuctionEngine.on_bar, detector.candidate29_on_bar)
            self.assertIs(detector._step, module._step)
            self.assertEqual(detector.SCENARIO_KIND, SCENARIO_KIND)
            self.assertIs(detector.BASE_ON_BAR, prior_on_bar)
        finally:
            (
                CausalAuctionEngine.on_bar,
                CausalAuctionEngine.mark_submitted,
                CausalAuctionEngine.mark_rejected,
                CausalAuctionEngine.mark_trade_terminal,
            ) = old_methods
            detector._step = old_step
            detector.SCENARIO_KIND = old_kind
            (
                detector.BASE_ON_BAR,
                detector.BASE_MARK_SUBMITTED,
                detector.BASE_MARK_REJECTED,
                detector.BASE_MARK_TRADE_TERMINAL,
            ) = old_bases


if __name__ == "__main__":
    unittest.main(verbosity=2)
