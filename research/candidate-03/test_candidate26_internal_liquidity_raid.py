from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
import unittest

import candidate26_internal_liquidity_raid as module
from candidate26_internal_liquidity_raid import (
    InternalRaidState,
    SCENARIO_KIND,
    _market_economics,
    _select_crossed_pivot,
    _step,
)
from logic import BarObs, CausalAuctionEngine, Direction, MINUTE_NS, Scenario, Side


class Candidate26InternalLiquidityRaidTests(unittest.TestCase):
    def test_cost_model_includes_entry_stop_and_target_fees(self) -> None:
        risk, loss, gain, net_r = _market_economics(
            direction=Direction.LONG,
            entry=100.0,
            stop=98.0,
            target=106.0,
            taker_rate=0.001,
            maker_rate=0.0005,
        )
        self.assertAlmostEqual(risk, 2.0)
        self.assertAlmostEqual(loss, 2.198)
        self.assertAlmostEqual(gain, 5.847)
        self.assertAlmostEqual(net_r, 5.847 / 2.198)

    def test_nearest_preknown_internal_pool_is_selected_once(self) -> None:
        now = 100 * MINUTE_NS
        engine = SimpleNamespace(
            config=SimpleNamespace(
                event_expiry_bars=60,
                sweep_min_atr=0.10,
                sweep_max_atr=1.00,
            ),
            internal_lows=[
                (70 * MINUTE_NS, 80 * MINUTE_NS, 98.5),
                (75 * MINUTE_NS, 85 * MINUTE_NS, 99.0),
                # Future-known pivots must never participate.
                (80 * MINUTE_NS, 101 * MINUTE_NS, 99.2),
            ],
            internal_highs=[],
        )
        previous = BarObs(now - MINUTE_NS, 100.5, 101.0, 100.0, 100.5, 100.0, 50.0)
        raid = BarObs(now, 100.5, 100.6, 98.4, 98.8, 200.0, 20.0)
        selected = _select_crossed_pivot(engine, raid, previous, 1.0, Direction.LONG)
        self.assertIsNotNone(selected)
        self.assertEqual(selected[2], 99.0)

        engine._candidate26_consumed_internal_keys.add(
            (Side.LOW.value, 75 * MINUTE_NS, 85 * MINUTE_NS, 99.0)
        )
        second = _select_crossed_pivot(engine, raid, previous, 1.0, Direction.LONG)
        self.assertIsNotNone(second)
        self.assertEqual(second[2], 98.5)

    def test_reclaim_requires_later_displacement_before_plan(self) -> None:
        events: list[tuple[object, ...]] = []
        target = SimpleNamespace(
            scenario_id="EXTERNAL-HIGH",
            consumed=False,
            expiry_index=1000,
        )
        engine = SimpleNamespace(
            config=SimpleNamespace(
                acceptance_close_atr=0.08,
                rejection_reclaim_atr=0.04,
                acceptance_min_closes=2,
                displacement_flow_min=0.10,
                acceptance_close_location=0.65,
                displacement_body_atr=0.45,
                stop_buffer_atr=0.08,
                effective_taker_rate=0.0008,
                effective_maker_rate=0.0004,
                min_stop_atr=0.18,
                min_net_r=1.25,
            ),
            pools=[target],
            skips=Counter(),
            _index=10,
            _candidate26_internal_raid_state=None,
            _event=lambda *args: events.append(args),
        )
        state = InternalRaidState(
            scenario_id="BTC-ILR-1-2-LONG",
            direction=Direction.LONG,
            trigger_side=Side.LOW,
            trigger_level=100.0,
            trigger_candidate_ts_ns=1,
            trigger_known_ts_ns=2,
            target_pool_id="EXTERNAL-HIGH",
            target_price=110.0,
            target_source="PREVIOUS_UTC_DAY",
            target_strength=4,
            draw_score=2.0,
            decision_end_ts_ns=1_000_000,
            sweep_ts_ns=100,
            sweep_index=9,
            expiry_index=100,
            sweep_extreme=99.0,
            episode_break_level=101.0,
        )
        engine._candidate26_internal_raid_state = state

        reclaim = BarObs(200, 99.6, 100.6, 99.0, 100.2, 100.0, 70.0)
        self.assertIsNone(_step(engine, state, reclaim, 1.0))
        self.assertEqual(state.state, "WAIT_DISPLACEMENT")
        self.assertEqual(state.reclaim_index, 10)

        # A same-index bar cannot become a confirmation plan.
        same_index = BarObs(250, 100.2, 101.8, 100.1, 101.6, 100.0, 90.0)
        self.assertIsNone(_step(engine, state, same_index, 1.0))

        engine._index = 11
        displacement = BarObs(300, 100.6, 101.8, 100.5, 101.6, 100.0, 90.0)
        plan = _step(engine, state, displacement, 1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.scenario, Scenario.AAC)
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertEqual(plan.entry_order_type, "MARKET")
        self.assertFalse(plan.entry_post_only)
        self.assertEqual(plan.details["scenario_kind"], SCENARIO_KIND)
        self.assertGreater(plan.net_r, 1.25)
        self.assertEqual(state.state, "PLAN_CONFIRMED")
        self.assertEqual(events[-1][1], "TRADE_PLAN_CONFIRMED")

    def test_install_captures_existing_lifecycle_hooks_at_install_time(self) -> None:
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
            self.assertIs(CausalAuctionEngine.on_bar, module.candidate26_on_bar)
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
