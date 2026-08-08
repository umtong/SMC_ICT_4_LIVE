from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
import unittest

import candidate27_quarter_hour_delivery as module
from candidate27_quarter_hour_delivery import (
    QuarterHourState,
    SCENARIO_KIND,
    _detect,
    _is_first_completed_minute_after_quarter,
    _market_economics,
    _step,
    _strict_external_target,
)
from logic import BarObs, CausalAuctionEngine, Direction, MINUTE_NS, Scenario, Side


class Candidate27QuarterHourDeliveryTests(unittest.TestCase):
    @staticmethod
    def config() -> SimpleNamespace:
        return SimpleNamespace(
            min_relative_volume=1.0,
            displacement_body_atr=0.20,
            acceptance_flow_min=0.06,
            acceptance_close_location=0.60,
            retrace_expiry_bars=12,
            acceptance_retest_atr=0.18,
            acceptance_hold_atr=0.02,
            acceptance_min_closes=2,
            reacceleration_flow_min=0.04,
            reacceleration_body_atr=0.18,
            stop_buffer_atr=0.08,
            effective_taker_rate=0.0008,
            effective_maker_rate=0.0004,
            min_stop_atr=0.08,
            min_net_r=1.25,
        )

    @staticmethod
    def target(**updates) -> SimpleNamespace:
        values = dict(
            scenario_id="PREEXISTING-HIGH",
            consumed=False,
            external=True,
            confirmed_index=1,
            expiry_index=1_000,
            source="PREVIOUS_UTC_DAY",
            side=Side.HIGH,
            level=106.0,
            strength=4,
        )
        values.update(updates)
        return SimpleNamespace(**values)

    def test_clock_maps_first_completed_minute_not_boundary_timestamp(self) -> None:
        for minute in (1, 16, 31, 46):
            self.assertTrue(
                _is_first_completed_minute_after_quarter(minute * MINUTE_NS)
            )
        for minute in (0, 2, 15, 17, 30, 32, 45, 47, 59):
            self.assertFalse(
                _is_first_completed_minute_after_quarter(minute * MINUTE_NS)
            )

    def test_cost_model_reserves_taker_entry_stop_and_maker_target(self) -> None:
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

    def test_target_must_preexist_and_be_strictly_ahead(self) -> None:
        stale = self.target(
            scenario_id="STALE",
            consumed=True,
            level=103.0,
        )
        current = self.target(
            scenario_id="CURRENT",
            confirmed_index=10,
            level=104.0,
        )
        nearest = self.target(
            scenario_id="NEAREST",
            level=105.0,
        )
        farther = self.target(
            scenario_id="FARTHER",
            level=110.0,
        )
        engine = SimpleNamespace(
            pools=[stale, current, farther, nearest],
            _index=10,
        )
        selected = _strict_external_target(engine, Direction.LONG, 101.0)
        self.assertIs(selected, nearest)

    def test_opening_context_and_later_confirmation_have_separate_bars(self) -> None:
        events: list[tuple[object, ...]] = []
        target = self.target()
        engine = SimpleNamespace(
            _candidate27_quarter_hour_state=None,
            active_trade_id=None,
            median_volume=100.0,
            config=self.config(),
            pools=[target],
            _index=10,
            skips=Counter(),
            instrument_id="BTCUSDT-PERP.BINANCE",
            _event=lambda *args: events.append(args),
        )
        opening = BarObs(
            16 * MINUTE_NS,
            100.0,
            101.2,
            99.8,
            101.0,
            200.0,
            180.0,
        )
        _detect(engine, opening, 1.0)
        state = engine._candidate27_quarter_hour_state
        self.assertIsNotNone(state)
        self.assertEqual(state.direction, Direction.LONG)
        self.assertEqual(events[-1][1], "QUARTER_HOUR_OPENING_IMBALANCE")

        continuation = BarObs(
            17 * MINUTE_NS,
            100.8,
            101.8,
            100.7,
            101.6,
            100.0,
            85.0,
        )
        # Even a continuation-looking observation cannot confirm at the opening
        # index; the state definition and transition evidence remain separate.
        self.assertIsNone(_step(engine, state, continuation, 1.0))

        engine._index = 11
        plan = _step(engine, state, continuation, 1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.scenario, Scenario.AAC)
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertEqual(plan.entry_order_type, "MARKET")
        self.assertFalse(plan.entry_post_only)
        self.assertEqual(plan.details["scenario_kind"], SCENARIO_KIND)
        self.assertGreater(plan.net_r, 1.25)
        self.assertEqual(events[-1][1], "TRADE_PLAN_CONFIRMED")

    def test_opening_leg_failure_is_terminal_before_entry(self) -> None:
        events: list[tuple[object, ...]] = []
        target = self.target()
        state = QuarterHourState(
            scenario_id="BTC-QHD-1",
            direction=Direction.LONG,
            boundary_ts_ns=0,
            opening_ts_ns=MINUTE_NS,
            opening_index=10,
            expiry_index=22,
            opening_open=100.0,
            opening_high=101.0,
            opening_low=99.5,
            opening_close=100.8,
            pullback_extreme=99.5,
            target_pool_id=target.scenario_id,
            target_price=target.level,
            target_source=target.source,
            target_strength=target.strength,
            opening_signed_flow=0.5,
            opening_relative_volume=2.0,
        )
        engine = SimpleNamespace(
            _candidate27_quarter_hour_state=state,
            config=self.config(),
            pools=[target],
            _index=11,
            skips=Counter(),
            _event=lambda *args: events.append(args),
        )
        failure = BarObs(
            2 * MINUTE_NS,
            99.8,
            100.0,
            99.0,
            99.2,
            100.0,
            20.0,
        )
        self.assertIsNone(_step(engine, state, failure, 1.0))
        self.assertIsNone(engine._candidate27_quarter_hour_state)
        self.assertEqual(
            events[-1][6],
            "QUARTER_HOUR_OPENING_LEG_INVALIDATED",
        )

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
            self.assertIs(CausalAuctionEngine.on_bar, module.candidate27_on_bar)
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
