from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import Candle, CostAssumptions, Side, TargetMode
from market_v7 import (
    EasyChartSessionTrapEngine,
    ExpiringArmedSetup,
    SessionLiquidityRange,
    SessionTrapConfig,
)
from simulator_v7 import ExpiringContinuousAccountSimulator, InstrumentSpec, MinuteBar


NS = 60_000_000_000


def bar(index, open_, high, low, close):
    start = index * 5 * NS
    return Candle(start, start + 5 * NS - 1, open_, high, low, close, 1.0)


def session_range(start_index=1, end_index=20):
    return SessionLiquidityRange(
        range_id="BTCUSDT:ASIA:2024-02-01",
        reference_family="ASIA_RANGE",
        trade_window="LONDON_KZ",
        observed_time_ns=0,
        trade_start_ns=start_index * 5 * NS,
        trade_end_ns=end_index * 5 * NS,
        high=110.0,
        low=100.0,
    )


class TestSessionTrapEngine(unittest.TestCase):
    def engine(self, **overrides):
        values = {
            "enable_immediate_fakeout": True,
            "enable_delayed_trap": True,
            "accepted_break_range_widths": 1.0,
            "tick_size": 0.1,
            "source_timeframe_minutes": 5,
        }
        values.update(overrides)
        return EasyChartSessionTrapEngine(
            "BTCUSDT",
            [session_range()],
            SessionTrapConfig(**values),
        )

    def test_immediate_fakeout_arms_first_boundary_retest(self):
        engine = self.engine(enable_delayed_trap=False)
        setups = engine.on_close(bar(1, 101.0, 102.0, 98.0, 101.0), 1)
        self.assertEqual(len(setups), 1)
        setup = setups[0]
        self.assertEqual(setup.side, Side.LONG)
        self.assertEqual(setup.entry, 100.0)
        self.assertEqual(setup.stop, 97.9)
        self.assertEqual(setup.initial_target, 110.0)
        self.assertEqual(setup.valid_until_ns, session_range().trade_end_ns)
        self.assertEqual(
            setup.family,
            "SESSION_ASIA_RANGE_LONDON_KZ_IMMEDIATE_FAKEOUT_RETEST",
        )
        plan = setup.executable(setup.initial_target, target_id=setup.fixed_target_id)
        self.assertIsNotNone(plan)

    def test_delayed_trap_can_wait_outside_until_reclaim(self):
        engine = self.engine(enable_immediate_fakeout=False)
        self.assertEqual(engine.on_close(bar(1, 101.0, 101.5, 99.0, 99.5), 1), [])
        self.assertEqual(engine.on_close(bar(2, 99.5, 100.0, 97.0, 98.0), 2), [])
        setups = engine.on_close(bar(3, 98.0, 101.0, 97.5, 100.5), 3)
        self.assertEqual(len(setups), 1)
        setup = setups[0]
        self.assertEqual(setup.stop, 96.9)
        self.assertIn("DELAYED_TRAP_RETEST", setup.family)

    def test_full_range_continuation_retires_reference(self):
        engine = self.engine(enable_immediate_fakeout=False)
        engine.on_close(bar(1, 101.0, 101.5, 99.0, 99.5), 1)
        setups = engine.on_close(bar(2, 99.5, 99.8, 89.0, 90.0), 2)
        self.assertEqual(setups, [])
        self.assertEqual(engine.active, {})
        self.assertEqual(engine.diagnostics.get("accepted_break_full_range"), 1)

    def test_two_sided_range_sweep_in_one_bar_is_unresolved(self):
        engine = self.engine()
        setups = engine.on_close(bar(1, 105.0, 111.0, 99.0, 105.0), 1)
        self.assertEqual(setups, [])
        self.assertEqual(engine.diagnostics.get("two_sided_same_bar_ambiguous"), 1)

    def test_setup_below_one_r_is_rejected_not_retargeted(self):
        narrow = SessionLiquidityRange(
            range_id="narrow",
            reference_family="ASIA_RANGE",
            trade_window="LONDON_KZ",
            observed_time_ns=0,
            trade_start_ns=5 * NS,
            trade_end_ns=100 * NS,
            high=102.0,
            low=100.0,
        )
        engine = EasyChartSessionTrapEngine(
            "BTCUSDT",
            [narrow],
            SessionTrapConfig(tick_size=0.1),
        )
        # Sweep depth 3.0 plus tick makes the opposite boundary less than 1R.
        setups = engine.on_close(bar(1, 101.0, 101.5, 97.0, 100.5), 1)
        self.assertEqual(setups, [])
        self.assertEqual(engine.diagnostics.get("gross_rr_lt_1"), 1)


class TestExpiringSimulator(unittest.TestCase):
    def test_unfilled_setup_is_removed_at_session_end(self):
        setup = ExpiringArmedSetup(
            setup_id="s",
            causal_event_id="c",
            symbol="BTCUSDT",
            family="SESSION_TEST",
            side=Side.LONG,
            observed_time_ns=1,
            entry=100.0,
            stop=99.0,
            target_mode=TargetMode.FIXED_STRUCTURE,
            initial_target=102.0,
            fixed_target_id="t",
            source_pool_id="p",
            zone_low=100.0,
            zone_high=100.0,
            formation_extreme=99.1,
            body_ratio=0.0,
            valid_until_ns=10,
        )
        simulator = ExpiringContinuousAccountSimulator(
            starting_nav=100_000.0,
            specs={
                "BTCUSDT": InstrumentSpec(
                    symbol="BTCUSDT",
                    tick_size=0.1,
                    size_increment=0.001,
                    min_quantity=0.001,
                    min_notional=5.0,
                ),
            },
            costs=CostAssumptions(),
        )
        simulator.add_setups([setup])
        simulator.on_timestamp(
            {
                "BTCUSDT": MinuteBar(
                    symbol="BTCUSDT",
                    ts_open_ns=10,
                    ts_close_ns=11,
                    open=101.0,
                    high=101.5,
                    low=100.5,
                    close=101.2,
                ),
            },
        )
        self.assertEqual(simulator.pending, {})
        self.assertEqual(simulator.diagnostics.get("setup_window_expired"), 1)
        self.assertEqual(len(simulator.trades), 0)


if __name__ == "__main__":
    unittest.main()
