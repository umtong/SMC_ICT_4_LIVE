"""Focused tests for the v2.3 first-retrace state machine."""

from __future__ import annotations

from dataclasses import replace
import unittest

from candidate import AuctionStateMachine
from candidate import BarView
from candidate import MachineParams
from candidate import TradePlan
from c10_retest_state import RetestPending


class RetestConfirmationTests(unittest.TestCase):
    @staticmethod
    def _plan() -> TradePlan:
        return TradePlan(
            scenario_id="SCENARIO-LONG",
            scenario="STRUCTURAL_SWEEP_REJECTION",
            direction=1,
            observed_ns=1,
            entry_estimate=101.0,
            stop_price=95.0,
            target_price=110.0,
            boundary=96.0,
            atr=2.0,
            structural_target="CONFIRMED_HIGH_LIQUIDITY_POOL",
            entry_order_type="LIMIT",
            entry_expiry_bars=16,
            invalidation_price=95.0,
            details={"zone_low": 100.0, "zone_high": 102.0},
        )

    def _machine(self) -> AuctionStateMachine:
        params = replace(
            MachineParams(),
            enable_retrace_confirmation=True,
            maker_fee=0.0,
            taker_fee=0.0,
            min_net_rr=0.1,
        )
        return AuctionStateMachine(params, tick_size=0.1, instrument_id="TEST")

    def test_corridor_touch_then_prior_extreme_break_arms_passive_entry(self) -> None:
        machine = self._machine()
        plan = self._plan()
        machine.bar_index = 5
        machine.retest_pending = RetestPending(
            plan=plan,
            armed_index=0,
            armed_ns=1,
            zone_low=100.0,
            zone_high=102.0,
        )
        prior = BarView(10, 102.5, 103.0, 101.0, 101.5, 1.0)
        confirmation = BarView(11, 101.0, 104.0, 100.5, 103.5, 1.0)
        machine.history.extend([prior, confirmation])

        events, confirmed = machine._process_retest(confirmation)
        self.assertEqual(
            [event.event_type for event in events],
            ["RETRACE_TOUCHED", "RETRACE_CONFIRMED", "ENTRY_READY"],
        )
        self.assertIsNotNone(confirmed)
        assert confirmed is not None
        self.assertEqual(confirmed.entry_estimate, 102.0)
        self.assertEqual(confirmed.observed_ns, confirmation.ts_ns)
        self.assertEqual(confirmed.entry_expiry_bars, 11)
        self.assertIsNone(machine.retest_pending)

    def test_touch_without_rejection_does_not_create_entry(self) -> None:
        machine = self._machine()
        plan = self._plan()
        machine.bar_index = 5
        machine.retest_pending = RetestPending(
            plan=plan,
            armed_index=0,
            armed_ns=1,
            zone_low=100.0,
            zone_high=102.0,
        )
        prior = BarView(10, 102.5, 103.0, 101.0, 101.5, 1.0)
        failed_touch = BarView(11, 102.0, 102.5, 99.0, 100.0, 1.0)
        machine.history.extend([prior, failed_touch])

        events, confirmed = machine._process_retest(failed_touch)
        self.assertEqual([event.event_type for event in events], ["RETRACE_TOUCHED"])
        self.assertIsNone(confirmed)
        self.assertIsNotNone(machine.retest_pending)

        machine.bar_index += 1
        invalidation = BarView(12, 96.0, 96.5, 94.0, 94.5, 1.0)
        machine.history.append(invalidation)
        events, confirmed = machine._process_retest(invalidation)
        self.assertEqual([event.event_type for event in events], ["SCENARIO_INVALIDATED"])
        self.assertIsNone(confirmed)
        self.assertIsNone(machine.retest_pending)


if __name__ == "__main__":
    unittest.main()
