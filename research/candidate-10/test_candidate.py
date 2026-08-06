"""Pure causal-state tests; execution integration is covered by the workflow run."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import unittest

from candidate import AuctionRange
from candidate import AuctionStateMachine
from candidate import BarView
from candidate import MachineParams
from candidate import reproducible_weeks


class Candidate10Tests(unittest.TestCase):
    def test_week_selection_is_stable(self) -> None:
        self.assertEqual(
            reproducible_weeks(),
            [date(2023, 10, 16), date(2023, 5, 15), date(2024, 1, 15)],
        )

    def test_rejection_requires_raid_then_displacement_and_arms_limit(self) -> None:
        params = replace(
            MachineParams(),
            atr_lookback=20,
            block_minutes=20,
            raid_atr=0.05,
            displacement_atr=0.5,
            stop_buffer_atr=0.25,
            maker_fee=0.0,
            taker_fee=0.0,
            min_net_rr=0.1,
            enable_acceptance=False,
        )
        machine = AuctionStateMachine(params, tick_size=0.1, instrument_id="TEST")
        block_ns = params.block_minutes * 60_000_000_000
        ts = (1_700_000_000_000_000_000 // block_ns) * block_ns + 60_000_000_000
        for i in range(40):
            block_position = i % 20
            base = 100.0 + block_position * 0.5
            machine.on_bar(
                BarView(
                    ts + i * 60_000_000_000,
                    base,
                    base + 0.4,
                    base - 0.4,
                    base + 0.1,
                    1.0,
                ),
            )
        pool = machine.previous_range
        self.assertIsNotNone(pool)
        assert pool is not None

        raid = BarView(
            ts + 40 * 60_000_000_000,
            pool.high - 0.2,
            pool.high + 1.0,
            pool.high - 1.0,
            pool.high - 0.5,
            1.0,
        )
        displacement = BarView(
            ts + 41 * 60_000_000_000,
            pool.high - 0.5,
            pool.high - 0.2,
            pool.high - 4.5,
            pool.high - 4.2,
            1.0,
        )
        first_events, first_plan = machine.on_bar(raid)
        second_events, plan = machine.on_bar(displacement)
        self.assertIsNone(first_plan)
        self.assertEqual(first_events[0].event_type, "LIQUIDITY_EVENT")
        self.assertIn(
            "DISPLACEMENT_CONFIRMED",
            [item.event_type for item in second_events],
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.entry_order_type, "LIMIT")
        self.assertGreater(plan.entry_estimate, displacement.close)
        self.assertGreater(plan.stop_price, pool.high)
        self.assertGreater(plan.entry_expiry_bars, 0)

    def test_ablation_disables_acceptance_creation(self) -> None:
        params = replace(MachineParams(), enable_acceptance=False)
        machine = AuctionStateMachine(params, tick_size=0.1, instrument_id="TEST")
        machine.previous_range = AuctionRange(1, 0, 1, 100.0, 110.0, 90.0, 100.0, 240)
        machine.current_range = AuctionRange(2, 2, 2, 100.0, 100.0, 100.0, 100.0, 1)
        for i in range(100):
            machine.true_ranges.append(1.0)
            machine.history.append(BarView(i, 100.0, 100.5, 99.5, 100.0, 1.0))
        machine.bar_index = 100
        events = machine._detect_setup(
            BarView(101, 110.0, 112.0, 109.8, 111.5, 1.0),
            1.0,
        )
        self.assertEqual(events, [])
        self.assertIsNone(machine.active)

    def test_acceptance_needs_two_distinct_closes_then_arms_boundary_limit(self) -> None:
        params = replace(
            MachineParams(),
            enable_rejection=False,
            maker_fee=0.0,
            taker_fee=0.0,
            stop_buffer_atr=0.5,
            min_net_rr=0.1,
        )
        machine = AuctionStateMachine(params, tick_size=0.1, instrument_id="TEST")
        machine.previous_range = AuctionRange(1, 0, 1, 100.0, 110.0, 90.0, 100.0, 240)
        machine.current_range = AuctionRange(2, 2, 2, 100.0, 100.0, 100.0, 100.0, 1)
        machine.past_ranges.append(machine.previous_range)
        for i in range(100):
            machine.true_ranges.append(1.0)
            machine.history.append(BarView(i, 100.0, 100.5, 99.5, 100.0, 1.0))
        machine.bar_index = 100
        first = BarView(101, 110.0, 112.0, 109.8, 111.5, 1.0)
        events = machine._detect_setup(first, 1.0)
        self.assertEqual([item.event_type for item in events], ["LIQUIDITY_EVENT"])
        assert machine.active is not None
        more, plan = machine._process_acceptance(first, 1.0)
        self.assertEqual(more, [])
        self.assertIsNone(plan)
        self.assertEqual(machine.active.consecutive_closes, 1)

        second = BarView(102, 111.5, 113.0, 111.0, 112.2, 1.0)
        more, plan = machine._process_acceptance(second, 1.0)
        self.assertIn("ACCEPTANCE_CONFIRMED", [item.event_type for item in more])
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.entry_order_type, "LIMIT")
        self.assertEqual(plan.entry_estimate, 110.0)
        self.assertLess(plan.stop_price, plan.entry_estimate)

    def test_execution_buffer_covers_noise_and_round_trip_cost_floor(self) -> None:
        params = replace(
            MachineParams(),
            stop_buffer_atr=0.5,
            maker_fee=0.0004,
            taker_fee=0.0007,
            execution_reserve_ticks=2,
        )
        machine = AuctionStateMachine(params, tick_size=0.1, instrument_id="TEST")
        buffer_value = machine._execution_buffer(30_000.0, 20.0)
        cost_floor = 30_000.0 * (0.0004 + 0.0007) + 0.2
        self.assertGreaterEqual(buffer_value, cost_floor)
        self.assertGreaterEqual(buffer_value, 10.0)

    def test_net_rr_rejects_apparent_reward_consumed_by_cost(self) -> None:
        params = replace(
            MachineParams(),
            maker_fee=0.001,
            taker_fee=0.001,
            execution_reserve_ticks=2,
        )
        machine = AuctionStateMachine(params, tick_size=0.1, instrument_id="TEST")
        net_rr = machine._net_rr(direction=1, entry=100.0, stop=99.8, target=100.3)
        self.assertLess(net_rr, 0.0)


if __name__ == "__main__":
    unittest.main()
