from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import ArmedSetup, CostAssumptions, Side, TargetMode
from simulator_v3 import ContinuousAccountSimulator, InstrumentSpec, MinuteBar


class TestDynamicImpulseTarget(unittest.TestCase):
    def setUp(self):
        self.sim = ContinuousAccountSimulator(
            starting_nav=100_000.0,
            specs={"BTCUSDT": InstrumentSpec("BTCUSDT", 0.1, "0.001", 0.001, 10.0)},
            costs=CostAssumptions(
                entry_fee_bps=2.0,
                stop_fee_bps=5.0,
                target_fee_bps=2.0,
                stop_slippage_bps=2.5,
                expected_funding_bps=1.0,
            ),
        )

    def setup(self, *, setup_id="s", causal="c", target=104.0, mode=TargetMode.IMPULSE_EXTREME):
        return ArmedSetup(
            setup_id=setup_id,
            causal_event_id=causal,
            symbol="BTCUSDT",
            family="SWEEP_RECLAIM_OB",
            side=Side.LONG,
            observed_time_ns=1,
            entry=100.0,
            stop=98.0,
            target_mode=mode,
            initial_target=target,
            fixed_target_id="fixed",
            source_pool_id="source",
            zone_low=99.0,
            zone_high=100.0,
            formation_extreme=98.0,
            body_ratio=1.8,
            previous_body=1.0,
            current_body=1.8,
        )

    def test_favorable_extreme_before_first_touch_becomes_target(self):
        self.sim.add_setups([self.setup()])
        # Adaptive path: 103 -> 106 -> 99.5 -> 100.5.  High 106 occurs
        # causally before entry 100, so executable target is 106 (3R).
        self.sim.on_minute(
            symbol="BTCUSDT", ts_open_ns=2, ts_close_ns=60_000_000_002,
            open_=103.0, high=106.0, low=99.5, close=100.5,
        )
        self.assertIsNotNone(self.sim.position)
        self.assertEqual(self.sim.position.plan.target, 106.0)
        self.assertEqual(self.sim.position.plan.gross_rr, 3.0)

    def test_later_same_bar_high_is_not_added_to_pre_entry_target(self):
        self.sim.add_setups([self.setup()])
        # Adaptive path: 103 -> 99.5 -> 108 -> 107.  Entry is touched before
        # 108, so target remains the already-known 104 and is hit after entry.
        self.sim.on_minute(
            symbol="BTCUSDT", ts_open_ns=2, ts_close_ns=60_000_000_002,
            open_=103.0, high=108.0, low=99.5, close=107.0,
        )
        self.assertIsNone(self.sim.position)
        self.assertEqual(len(self.sim.trades), 1)
        self.assertEqual(self.sim.trades[0].target, 104.0)
        self.assertEqual(self.sim.trades[0].outcome, "TARGET")

    def test_rr_gate_is_evaluated_at_first_entry_touch(self):
        self.sim.add_setups([self.setup(target=101.0)])
        self.sim.on_minute(
            symbol="BTCUSDT", ts_open_ns=2, ts_close_ns=60_000_000_002,
            open_=101.0, high=101.2, low=99.5, close=100.2,
        )
        self.assertIsNone(self.sim.position)
        self.assertEqual(len(self.sim.trades), 0)
        self.assertEqual(self.sim.diagnostics["rr_lt_1_at_entry"], 1)
        self.assertEqual(len(self.sim.pending), 0)

    def test_fixed_target_consumed_before_first_touch_cancels(self):
        self.sim.add_setups([self.setup(mode=TargetMode.FIXED_STRUCTURE, target=104.0)])
        self.sim.on_minute(
            symbol="BTCUSDT", ts_open_ns=2, ts_close_ns=60_000_000_002,
            open_=105.0, high=106.0, low=104.5, close=105.5,
        )
        self.assertEqual(self.sim.diagnostics["target_consumed"], 1)
        self.assertIsNone(self.sim.position)
        self.assertEqual(len(self.sim.pending), 0)

    def test_first_retest_is_consumed_when_global_slot_is_busy(self):
        first = self.setup(setup_id="first", causal="first")
        second = self.setup(setup_id="second", causal="second")
        self.sim.add_setups([first])
        self.sim.on_minute(
            symbol="BTCUSDT", ts_open_ns=2, ts_close_ns=60_000_000_002,
            open_=103.0, high=106.0, low=99.5, close=100.5,
        )
        self.assertIsNotNone(self.sim.position)
        self.sim.add_setups([second])
        self.sim.on_timestamp({
            "BTCUSDT": MinuteBar(
                "BTCUSDT", 60_000_000_003, 120_000_000_002,
                101.0, 101.5, 99.5, 100.5,
            ),
        })
        self.assertNotIn("second", self.sim.pending)
        self.assertEqual(self.sim.diagnostics["first_retest_missed_global_slot_busy"], 1)


if __name__ == "__main__":
    unittest.main()
