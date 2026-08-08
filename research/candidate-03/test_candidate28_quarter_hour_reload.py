from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
import unittest

import candidate28_quarter_hour_reload as module
from candidate28_quarter_hour_reload import (
    QuarterHourReloadState,
    SCENARIO_KIND,
    _confirm_extension,
    _detect_context,
    _detect_raid,
    _select_reload_pivot,
    _step,
)
from logic import BarObs, CausalAuctionEngine, Direction, MINUTE_NS, Scenario, Side


class Candidate28QuarterHourReloadTests(unittest.TestCase):
    @staticmethod
    def config() -> SimpleNamespace:
        return SimpleNamespace(
            min_relative_volume=1.0,
            displacement_body_atr=0.20,
            acceptance_flow_min=0.06,
            acceptance_close_location=0.60,
            event_expiry_bars=360,
            reacceleration_flow_min=0.04,
            reacceleration_body_atr=0.18,
            sweep_min_atr=0.05,
            sweep_max_atr=2.50,
            absorption_flow_min=0.08,
            acceptance_close_atr=0.08,
            rejection_reclaim_atr=0.05,
            acceptance_min_closes=2,
            stop_buffer_atr=0.08,
            effective_taker_rate=0.0008,
            effective_maker_rate=0.0004,
            min_stop_atr=0.08,
            min_net_r=1.25,
            acceptance_retest_atr=0.18,
            acceptance_hold_atr=0.02,
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
            level=110.0,
            strength=4,
        )
        values.update(updates)
        return SimpleNamespace(**values)

    @classmethod
    def engine(cls, *, index: int = 10, target=None):
        events: list[tuple[object, ...]] = []
        target = target or cls.target()
        engine = SimpleNamespace(
            _candidate28_reload_state=None,
            _candidate28_consumed_reload_pivots=set(),
            active_trade_id=None,
            median_volume=100.0,
            config=cls.config(),
            pools=[target],
            _index=index,
            skips=Counter(),
            instrument_id="BTCUSDT-PERP.BINANCE",
            internal_lows=[],
            internal_highs=[],
            _event=lambda *args: events.append(args),
        )
        return engine, events

    def test_clock_context_only_arms_and_freezes_preexisting_target(self) -> None:
        engine, events = self.engine()
        opening = BarObs(
            16 * MINUTE_NS,
            100.0,
            101.2,
            99.8,
            101.0,
            200.0,
            180.0,
        )
        _detect_context(engine, opening, 1.0)
        state = engine._candidate28_reload_state
        self.assertIsNotNone(state)
        self.assertEqual(state.state, "WAIT_EXTENSION")
        self.assertEqual(state.direction, Direction.LONG)
        self.assertEqual(state.target_pool_id, "PREEXISTING-HIGH")
        self.assertEqual(events[-1][1], "QUARTER_HOUR_RELOAD_CONTEXT_ARMED")

    def test_extension_is_context_confirmation_not_an_entry(self) -> None:
        engine, events = self.engine(index=10)
        state = QuarterHourReloadState(
            scenario_id="BTC-QHR-1",
            direction=Direction.LONG,
            boundary_ts_ns=15 * MINUTE_NS,
            opening_ts_ns=16 * MINUTE_NS,
            opening_index=10,
            expiry_index=370,
            opening_open=100.0,
            opening_high=101.2,
            opening_low=99.8,
            opening_close=101.0,
            opening_signed_flow=0.8,
            opening_relative_volume=2.0,
            target_pool_id="PREEXISTING-HIGH",
            target_price=110.0,
            target_source="PREVIOUS_UTC_DAY",
            target_strength=4,
        )
        extension = BarObs(
            17 * MINUTE_NS,
            101.0,
            102.0,
            100.9,
            101.8,
            150.0,
            130.0,
        )
        self.assertFalse(_confirm_extension(engine, state, extension, 1.0))
        engine._index = 11
        self.assertTrue(_confirm_extension(engine, state, extension, 1.0))
        self.assertEqual(state.state, "WAIT_RAID")
        self.assertEqual(events[-1][1], "QUARTER_HOUR_DELIVERY_LEG_CONFIRMED")

    def test_reload_pivot_belongs_to_delivery_leg_is_preknown_and_not_reused(self) -> None:
        engine, events = self.engine(index=12)
        opening_ts = 16 * MINUTE_NS
        state = QuarterHourReloadState(
            scenario_id="BTC-QHR-2",
            direction=Direction.LONG,
            boundary_ts_ns=15 * MINUTE_NS,
            opening_ts_ns=opening_ts,
            opening_index=10,
            expiry_index=370,
            opening_open=100.0,
            opening_high=101.2,
            opening_low=99.8,
            opening_close=101.0,
            opening_signed_flow=0.8,
            opening_relative_volume=2.0,
            target_pool_id="PREEXISTING-HIGH",
            target_price=110.0,
            target_source="PREVIOUS_UTC_DAY",
            target_strength=4,
            state="WAIT_RAID",
            extension_ts_ns=17 * MINUTE_NS,
            extension_index=11,
            extension_extreme=102.0,
        )
        engine.internal_lows = [
            (opening_ts - MINUTE_NS, 16 * MINUTE_NS, 101.1),
            (opening_ts + 1, 17 * MINUTE_NS, 101.2),
            (opening_ts + 2, 17 * MINUTE_NS, 100.9),
            (opening_ts + 3, 18 * MINUTE_NS, 101.3),
        ]
        prev = BarObs(17 * MINUTE_NS, 101.5, 102.0, 101.4, 101.6, 100.0, 70.0)
        raid = BarObs(18 * MINUTE_NS, 101.6, 101.8, 100.8, 101.0, 150.0, 15.0)
        selected = _select_reload_pivot(engine, state, raid, prev, 1.0)
        self.assertIsNotNone(selected)
        self.assertAlmostEqual(selected[2], 101.2)
        self.assertTrue(_detect_raid(engine, state, raid, prev, 1.0))
        self.assertEqual(state.state, "WAIT_RECLAIM")
        self.assertEqual(events[-1][1], "QUARTER_HOUR_INTERNAL_RELOAD_RAID")

        later = QuarterHourReloadState(
            scenario_id="BTC-QHR-3",
            direction=Direction.LONG,
            boundary_ts_ns=30 * MINUTE_NS,
            opening_ts_ns=opening_ts,
            opening_index=20,
            expiry_index=400,
            opening_open=100.0,
            opening_high=102.0,
            opening_low=99.8,
            opening_close=101.5,
            opening_signed_flow=0.8,
            opening_relative_volume=2.0,
            target_pool_id="PREEXISTING-HIGH",
            target_price=110.0,
            target_source="PREVIOUS_UTC_DAY",
            target_strength=4,
            state="WAIT_RAID",
            extension_ts_ns=31 * MINUTE_NS,
            extension_index=21,
            extension_extreme=102.5,
        )
        engine._index = 22
        another = _select_reload_pivot(engine, later, raid, prev, 1.0)
        self.assertIsNotNone(another)
        self.assertNotAlmostEqual(another[2], 101.2)

    def test_full_sequence_requires_distinct_extension_raid_reclaim_and_reload(self) -> None:
        engine, events = self.engine(index=10)
        opening_ts = 16 * MINUTE_NS
        opening = BarObs(opening_ts, 100.0, 101.2, 99.8, 101.0, 200.0, 180.0)
        _detect_context(engine, opening, 1.0)
        state = engine._candidate28_reload_state

        engine._index = 11
        extension = BarObs(17 * MINUTE_NS, 101.0, 102.1, 100.9, 101.9, 160.0, 140.0)
        self.assertIsNone(_step(engine, state, extension, opening, 1.0))
        self.assertEqual(state.state, "WAIT_RAID")

        engine.internal_lows = [(opening_ts + 1, 17 * MINUTE_NS, 101.2)]
        engine._index = 12
        raid = BarObs(18 * MINUTE_NS, 101.8, 102.0, 100.8, 101.0, 160.0, 15.0)
        self.assertIsNone(_step(engine, state, raid, extension, 1.0))
        self.assertEqual(state.state, "WAIT_RECLAIM")

        engine._index = 13
        reclaim = BarObs(19 * MINUTE_NS, 101.0, 101.8, 100.9, 101.5, 120.0, 90.0)
        self.assertIsNone(_step(engine, state, reclaim, raid, 1.0))
        self.assertEqual(state.state, "WAIT_REACCELERATION")

        engine._index = 14
        reload_bar = BarObs(20 * MINUTE_NS, 101.5, 103.0, 101.4, 102.8, 160.0, 145.0)
        plan = _step(engine, state, reload_bar, reclaim, 1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.scenario, Scenario.AAC)
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertEqual(plan.entry_order_type, "MARKET")
        self.assertFalse(plan.entry_post_only)
        self.assertEqual(plan.details["scenario_kind"], SCENARIO_KIND)
        self.assertEqual(plan.details["sweep_ts_ns"], raid.ts_ns)
        self.assertGreater(plan.net_r, 1.25)
        self.assertEqual(events[-1][1], "TRADE_PLAN_CONFIRMED")

    def test_delivery_leg_must_remain_valid_before_reload_raid(self) -> None:
        engine, events = self.engine(index=12)
        state = QuarterHourReloadState(
            scenario_id="BTC-QHR-4",
            direction=Direction.LONG,
            boundary_ts_ns=15 * MINUTE_NS,
            opening_ts_ns=16 * MINUTE_NS,
            opening_index=10,
            expiry_index=370,
            opening_open=100.0,
            opening_high=101.2,
            opening_low=99.8,
            opening_close=101.0,
            opening_signed_flow=0.8,
            opening_relative_volume=2.0,
            target_pool_id="PREEXISTING-HIGH",
            target_price=110.0,
            target_source="PREVIOUS_UTC_DAY",
            target_strength=4,
            state="WAIT_RAID",
            extension_ts_ns=17 * MINUTE_NS,
            extension_index=11,
            extension_extreme=102.0,
        )
        engine._candidate28_reload_state = state
        prev = BarObs(17 * MINUTE_NS, 101.0, 102.0, 100.9, 101.8, 100.0, 70.0)
        failed = BarObs(18 * MINUTE_NS, 101.0, 101.1, 99.0, 99.5, 120.0, 20.0)
        self.assertIsNone(_step(engine, state, failed, prev, 1.0))
        self.assertIsNone(engine._candidate28_reload_state)
        self.assertEqual(
            events[-1][6],
            "QUARTER_HOUR_RELOAD_DELIVERY_LEG_INVALIDATED_BEFORE_RAID",
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
            self.assertIs(CausalAuctionEngine.on_bar, module.candidate28_on_bar)
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
