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

    def test_rejection_requires_displacement_then_retrace(self) -> None:
        params = replace(
            MachineParams(),
            atr_lookback=20,
            block_minutes=20,
            raid_atr=0.05,
            displacement_atr=0.5,
            min_net_rr=0.5,
            enable_acceptance=False,
        )
        machine = AuctionStateMachine(params, tick_size=0.1, instrument_id="TEST")
        block_ns = params.block_minutes * 60_000_000_000
        ts = (1_700_000_000_000_000_000 // block_ns) * block_ns + 60_000_000_000
        # Warm-up and two completed blocks around 100-110. ``ts`` is the first
        # close timestamp, so the block membership uses ``ts - one minute``.
        for i in range(40):
            block_position = i % 20
            base = 100.0 + block_position * 0.5
            bar = BarView(
                ts + i * 60_000_000_000,
                base,
                base + 0.4,
                base - 0.4,
                base + 0.1,
                1.0,
            )
            machine.on_bar(bar)
        pool = machine.previous_range
        self.assertIsNotNone(pool)
        assert pool is not None

        # Raid prior high, re-enter, then bearish displacement, then first retrace.
        bars = [
            BarView(
                ts + 40 * 60_000_000_000,
                pool.high - 0.2,
                pool.high + 1.0,
                pool.high - 1.0,
                pool.high - 0.5,
                1.0,
            ),
            BarView(
                ts + 41 * 60_000_000_000,
                pool.high - 0.5,
                pool.high - 0.2,
                pool.high - 4.5,
                pool.high - 4.2,
                1.0,
            ),
            BarView(
                ts + 42 * 60_000_000_000,
                pool.high - 1.2,
                pool.high - 0.8,
                pool.high - 2.7,
                pool.high - 2.2,
                1.0,
            ),
        ]
        events = []
        plan = None
        for bar in bars:
            emitted, maybe_plan = machine.on_bar(bar)
            events.extend(emitted)
            plan = maybe_plan or plan
        event_types = [item.event_type for item in events]
        self.assertIn("LIQUIDITY_EVENT", event_types)
        self.assertIn("DISPLACEMENT_CONFIRMED", event_types)
        self.assertIsNotNone(plan)

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

    def test_acceptance_needs_two_distinct_closes(self) -> None:
        params = replace(MachineParams(), enable_rejection=False)
        machine = AuctionStateMachine(params, tick_size=0.1, instrument_id="TEST")
        machine.previous_range = AuctionRange(1, 0, 1, 100.0, 110.0, 90.0, 100.0, 240)
        machine.current_range = AuctionRange(2, 2, 2, 100.0, 100.0, 100.0, 100.0, 1)
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


if __name__ == "__main__":
    unittest.main()
