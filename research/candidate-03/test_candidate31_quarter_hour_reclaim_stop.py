from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
import unittest

import candidate29_quarter_hour_failed_auction as detector
import candidate31_quarter_hour_reclaim_stop as module
from candidate31_quarter_hour_reclaim_stop import (
    SCENARIO_KIND,
    _reclaim_stop_plan,
    _stop_market_economics,
)
from logic import BarObs, CausalAuctionEngine, Direction, MINUTE_NS, Scenario, Side


class Candidate31QuarterHourReclaimStopTests(unittest.TestCase):
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
                rejection_reclaim_atr=0.05,
                stop_buffer_atr=0.08,
                effective_taker_rate=0.0008,
                effective_maker_rate=0.0004,
                min_stop_atr=0.08,
                min_net_r=1.25,
            ),
            _index=20,
            skips=Counter(),
            _candidate29_quarter_hour_failed_state=None,
            _event=lambda *args: events.append(args),
        )
        return engine, events

    def test_short_stop_parent_is_placed_before_future_reclaim(self) -> None:
        engine, events = self.engine()
        state = self.state(direction=Direction.SHORT, swept_side=Side.HIGH)
        opening = BarObs(
            16 * MINUTE_NS,
            105.0,
            106.0,
            104.8,
            105.6,
            200.0,
            180.0,
        )
        plan = _reclaim_stop_plan(engine, state, opening, 1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.scenario, Scenario.FAR)
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.entry_order_type, "STOP_MARKET")
        self.assertFalse(plan.entry_post_only)
        self.assertAlmostEqual(plan.expected_entry, 104.95)
        self.assertGreater(plan.stop_price, 106.0)
        self.assertEqual(plan.target_price, 99.0)
        self.assertEqual(plan.details["scenario_kind"], SCENARIO_KIND)
        self.assertEqual(plan.details["entry_trigger_price"], plan.expected_entry)
        self.assertEqual(
            plan.expire_ts_ns,
            state.boundary_ts_ns + 15 * MINUTE_NS,
        )
        self.assertGreater(plan.net_r, 1.25)
        self.assertEqual(events[-1][1], "TRADE_PLAN_CONFIRMED")

    def test_already_crossed_trigger_is_never_backfilled(self) -> None:
        engine, events = self.engine()
        state = self.state(direction=Direction.SHORT, swept_side=Side.HIGH)
        opening = BarObs(
            16 * MINUTE_NS,
            105.0,
            106.0,
            104.6,
            104.7,
            200.0,
            180.0,
        )
        self.assertIsNone(_reclaim_stop_plan(engine, state, opening, 1.0))
        self.assertIsNone(engine._candidate29_quarter_hour_failed_state)
        self.assertEqual(
            events[-1][6],
            "QUARTER_HOUR_RECLAIM_TRIGGER_ALREADY_CROSSED_AT_SUBMISSION",
        )

    def test_long_contract_is_directionally_symmetric(self) -> None:
        engine, _ = self.engine()
        state = self.state(direction=Direction.LONG, swept_side=Side.LOW)
        opening = BarObs(
            16 * MINUTE_NS,
            93.0,
            93.2,
            92.0,
            92.4,
            200.0,
            20.0,
        )
        plan = _reclaim_stop_plan(engine, state, opening, 1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertAlmostEqual(plan.expected_entry, 93.05)
        self.assertLess(plan.stop_price, 92.0)
        self.assertEqual(plan.target_price, 99.0)

    def test_stop_market_costs_reserve_taker_entry_and_stop(self) -> None:
        risk, loss, gain, net_r = _stop_market_economics(
            direction=Direction.SHORT,
            entry=105.0,
            stop=106.0,
            target=99.0,
            taker_rate=0.0008,
            maker_rate=0.0004,
        )
        self.assertAlmostEqual(risk, 1.0)
        self.assertAlmostEqual(loss, 1.1688)
        self.assertAlmostEqual(gain, 5.8764)
        self.assertAlmostEqual(net_r, 5.8764 / 1.1688)

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
            self.assertIs(detector._step, module._reclaim_stop_plan)
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
