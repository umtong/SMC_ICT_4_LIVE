from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain import CostAssumptions, Side, TradePlan
from simulator import ContinuousAccountSimulator, InstrumentSpec, MinuteBar


class TestSimulator(unittest.TestCase):
    def setUp(self):
        specs = {
            "BTCUSDT": InstrumentSpec("BTCUSDT", .1, "0.001", .001, 10),
            "ETHUSDT": InstrumentSpec("ETHUSDT", .01, "0.001", .001, 20),
        }
        self.sim = ContinuousAccountSimulator(
            starting_nav=100000,
            specs=specs,
            costs=CostAssumptions(),
            leverage=20,
        )

    def plan(
        self,
        *,
        plan_id="p",
        causal="c",
        symbol="BTCUSDT",
        observed=1,
        entry=100,
        stop=98,
        target=104,
        rr=2,
    ):
        return TradePlan(
            plan_id,
            causal,
            symbol,
            "SWEEP",
            Side.LONG,
            observed,
            entry,
            stop,
            target,
            rr,
            "s",
            "t",
            99,
            100,
            98,
            2,
        )

    def test_single_full_exit_target(self):
        self.sim.add_plans([self.plan()])
        self.sim.on_minute(
            symbol="BTCUSDT",
            ts_open_ns=2,
            ts_close_ns=60_000_000_002,
            open_=101,
            high=101.5,
            low=99.5,
            close=100.5,
        )
        self.assertIsNotNone(self.sim.position)
        self.sim.on_minute(
            symbol="BTCUSDT",
            ts_open_ns=60_000_000_003,
            ts_close_ns=120_000_000_002,
            open_=100.5,
            high=104.2,
            low=100,
            close=104,
        )
        self.assertIsNone(self.sim.position)
        self.assertEqual(len(self.sim.trades), 1)
        self.assertEqual(self.sim.trades[0].outcome, "TARGET")

    def test_duplicate_causal_episode_not_counted_twice(self):
        p = self.plan()
        p2 = self.plan(plan_id="p2", causal=p.causal_event_id)
        self.sim.add_plans([p, p2])
        self.assertEqual(len(self.sim.pending), 1)

    def test_same_timestamp_arbitrates_all_symbols_not_input_order(self):
        lower_rr = self.plan(plan_id="btc", causal="btc", symbol="BTCUSDT", target=102, rr=1)
        higher_rr = self.plan(
            plan_id="eth",
            causal="eth",
            symbol="ETHUSDT",
            entry=100,
            stop=98,
            target=106,
            rr=3,
        )
        self.sim.add_plans([lower_rr, higher_rr])
        bars = {
            # Insert BTC first deliberately.  ETH must still win by RR.
            "BTCUSDT": MinuteBar("BTCUSDT", 2, 60, 101, 101.5, 99.5, 100.5),
            "ETHUSDT": MinuteBar("ETHUSDT", 2, 60, 101, 101.5, 99.5, 100.5),
        }
        self.sim.on_timestamp(bars)
        self.assertIsNotNone(self.sim.position)
        self.assertEqual(self.sim.position.plan.symbol, "ETHUSDT")
        self.assertNotIn("btc", self.sim.pending)
        self.assertEqual(self.sim.diagnostics["simultaneous_entry_conflicts"], 1)

    def test_first_retest_is_consumed_while_global_slot_busy(self):
        active = self.plan(plan_id="active", causal="active")
        waiting = self.plan(
            plan_id="waiting",
            causal="waiting",
            symbol="ETHUSDT",
            entry=100,
            stop=98,
            target=104,
            rr=2,
        )
        self.sim.add_plans([active])
        self.sim.on_minute(
            symbol="BTCUSDT", ts_open_ns=2, ts_close_ns=60, open_=101, high=101.2, low=99.5, close=100.5,
        )
        self.assertIsNotNone(self.sim.position)
        self.sim.add_plans([waiting])
        self.sim.on_timestamp(
            {
                "BTCUSDT": MinuteBar("BTCUSDT", 61, 120, 100.5, 101, 100, 100.6),
                "ETHUSDT": MinuteBar("ETHUSDT", 61, 120, 101, 101.5, 99.5, 100.5),
            },
        )
        self.assertNotIn("waiting", self.sim.pending)
        self.assertEqual(self.sim.diagnostics["first_retest_missed_global_slot_busy"], 1)

    def test_target_consumed_before_entry_cancels_plan(self):
        plan = self.plan()
        self.sim.add_plans([plan])
        self.sim.on_minute(
            symbol="BTCUSDT", ts_open_ns=2, ts_close_ns=60, open_=105, high=106, low=104.5, close=105.5,
        )
        self.assertIsNone(self.sim.position)
        self.assertNotIn(plan.plan_id, self.sim.pending)
        self.assertEqual(self.sim.diagnostics["target_consumed"], 1)


if __name__ == "__main__":
    unittest.main()
