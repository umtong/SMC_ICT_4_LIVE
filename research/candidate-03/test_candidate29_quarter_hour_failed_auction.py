from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
import unittest

import candidate29_quarter_hour_failed_auction as module
from candidate29_quarter_hour_failed_auction import (
    SCENARIO_KIND,
    _completed_previous_quarter_range,
    _detect,
    _is_first_completed_minute_after_quarter,
    _market_economics,
    _step,
)
from logic import BarObs, CausalAuctionEngine, Direction, MINUTE_NS, Scenario, Side


class Candidate29QuarterHourFailedAuctionTests(unittest.TestCase):
    def test_only_first_completed_minute_after_quarter_is_eligible(self) -> None:
        for minute in (1, 16, 31, 46):
            self.assertTrue(
                _is_first_completed_minute_after_quarter(minute * MINUTE_NS)
            )
        for minute in (0, 2, 15, 17, 30, 32, 45, 47, 59):
            self.assertFalse(
                _is_first_completed_minute_after_quarter(minute * MINUTE_NS)
            )

    def test_previous_range_uses_three_completed_five_minute_bars(self) -> None:
        bars = [
            SimpleNamespace(
                start_ts_ns=1 * MINUTE_NS,
                end_ts_ns=5 * MINUTE_NS,
                high=101.0,
                low=96.0,
            ),
            SimpleNamespace(
                start_ts_ns=6 * MINUTE_NS,
                end_ts_ns=10 * MINUTE_NS,
                high=104.0,
                low=94.0,
            ),
            SimpleNamespace(
                start_ts_ns=11 * MINUTE_NS,
                end_ts_ns=15 * MINUTE_NS,
                high=105.0,
                low=93.0,
            ),
            # Future/current observations must not enter the frozen source.
            SimpleNamespace(
                start_ts_ns=16 * MINUTE_NS,
                end_ts_ns=20 * MINUTE_NS,
                high=200.0,
                low=1.0,
            ),
        ]
        engine = SimpleNamespace(internal_bars=bars)
        source = _completed_previous_quarter_range(engine, 16 * MINUTE_NS)
        self.assertIsNotNone(source)
        self.assertEqual(source[0], 1 * MINUTE_NS)
        self.assertEqual(source[1], 15 * MINUTE_NS)
        self.assertEqual(source[2], 105.0)
        self.assertEqual(source[3], 93.0)
        self.assertEqual(source[4], 99.0)

    def test_high_sweep_reclaim_and_later_displacement_build_short_far(self) -> None:
        events: list[tuple[object, ...]] = []
        engine = SimpleNamespace(
            internal_bars=[
                SimpleNamespace(
                    start_ts_ns=1 * MINUTE_NS,
                    end_ts_ns=5 * MINUTE_NS,
                    high=101.0,
                    low=96.0,
                ),
                SimpleNamespace(
                    start_ts_ns=6 * MINUTE_NS,
                    end_ts_ns=10 * MINUTE_NS,
                    high=104.0,
                    low=94.0,
                ),
                SimpleNamespace(
                    start_ts_ns=11 * MINUTE_NS,
                    end_ts_ns=15 * MINUTE_NS,
                    high=105.0,
                    low=93.0,
                ),
            ],
            config=SimpleNamespace(
                min_relative_volume=0.85,
                sweep_min_atr=0.05,
                sweep_max_atr=2.50,
                absorption_flow_min=0.08,
                retrace_expiry_bars=12,
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
            median_volume=100.0,
            active_trade_id=None,
            _index=20,
            instrument_id="BTCUSDT-PERP.BINANCE",
            skips=Counter(),
            _candidate29_quarter_hour_failed_state=None,
            _event=lambda *args: events.append(args),
        )
        opening = BarObs(
            16 * MINUTE_NS,
            105.0,
            106.0,
            104.8,
            105.6,
            200.0,
            180.0,
        )
        _detect(engine, opening, 1.0)
        state = engine._candidate29_quarter_hour_failed_state
        self.assertIsNotNone(state)
        self.assertEqual(state.swept_side, Side.HIGH)
        self.assertEqual(state.direction, Direction.SHORT)
        self.assertEqual(state.range_midpoint, 99.0)

        engine._index = 21
        reclaim = BarObs(
            17 * MINUTE_NS,
            105.5,
            105.7,
            104.7,
            104.8,
            100.0,
            30.0,
        )
        self.assertIsNone(_step(engine, state, reclaim, 1.0))
        self.assertEqual(state.state, "WAIT_DISPLACEMENT")
        self.assertEqual(state.reclaim_index, 21)

        # Reclaim evidence cannot also be the displacement evidence.
        same_index = BarObs(
            17 * MINUTE_NS + 1,
            105.2,
            105.3,
            104.4,
            104.6,
            100.0,
            20.0,
        )
        self.assertIsNone(_step(engine, state, same_index, 1.0))

        engine._index = 22
        displacement = BarObs(
            18 * MINUTE_NS,
            105.2,
            105.3,
            104.4,
            104.6,
            100.0,
            20.0,
        )
        plan = _step(engine, state, displacement, 1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.scenario, Scenario.FAR)
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.entry_order_type, "MARKET")
        self.assertFalse(plan.entry_post_only)
        self.assertEqual(plan.details["scenario_kind"], SCENARIO_KIND)
        self.assertEqual(plan.target_price, 99.0)
        self.assertGreater(plan.net_r, 1.25)
        self.assertEqual(events[-1][1], "TRADE_PLAN_CONFIRMED")

    def test_cost_model_reserves_entry_stop_and_target_fees(self) -> None:
        risk, loss, gain, net_r = _market_economics(
            direction=Direction.SHORT,
            entry=104.0,
            stop=106.0,
            target=99.0,
            taker_rate=0.001,
            maker_rate=0.0005,
        )
        self.assertAlmostEqual(risk, 2.0)
        self.assertAlmostEqual(loss, 2.210)
        self.assertAlmostEqual(gain, 4.8465)
        self.assertAlmostEqual(net_r, 4.8465 / 2.210)

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
            self.assertIs(CausalAuctionEngine.on_bar, module.candidate29_on_bar)
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
