"""Focused tests for the v2.2 approach-structure replacement."""

from __future__ import annotations

from dataclasses import replace
import unittest

from candidate import AuctionStateMachine
from candidate import BarView
from candidate import LiquidityPool
from candidate import MachineParams


class MicroApproachStructureTests(unittest.TestCase):
    @staticmethod
    def _approach_bars() -> list[BarView]:
        # The oldest bar owns the whole-window low (90), but the nearest
        # right-confirmed pivot low is 97 at index 6.
        lows = [90.0, 96.0, 95.0, 96.0, 97.0, 98.0, 97.0, 98.0]
        highs = [99.0, 99.2, 99.1, 99.3, 99.4, 99.6, 99.5, 99.7]
        return [
            BarView(
                ts_ns=100 + index,
                open=98.5,
                high=highs[index],
                low=lows[index],
                close=98.8,
                volume=1.0,
            )
            for index in range(len(lows))
        ]

    def test_nearest_pivot_is_right_confirmed_and_not_window_extreme(self) -> None:
        machine = AuctionStateMachine(
            MachineParams(),
            tick_size=0.1,
            instrument_id="TEST",
        )
        machine.history.extend(self._approach_bars())
        level, metadata = machine._nearest_confirmed_micro_pivot(direction=-1)
        self.assertEqual(level, 97.0)
        self.assertEqual(
            metadata["approach_structure_type"],
            "RIGHT_CONFIRMED_MICRO_PIVOT_LOW",
        )
        self.assertEqual(metadata["micro_pivot_event_time_ns"], 106)
        self.assertEqual(metadata["micro_pivot_observed_time_ns"], 107)
        self.assertLessEqual(
            metadata["micro_pivot_observed_time_ns"],
            self._approach_bars()[-1].ts_ns,
        )

    def _machine(self, *, micro_pivot: bool) -> AuctionStateMachine:
        params = replace(
            MachineParams(),
            enable_nearest_micro_pivot=micro_pivot,
            enable_path_displacement=False,
            maker_fee=0.0,
            taker_fee=0.0,
            stop_buffer_atr=0.5,
            min_net_rr=0.1,
        )
        machine = AuctionStateMachine(
            params,
            tick_size=0.1,
            instrument_id="FULL" if micro_pivot else "ABLATION",
        )
        machine.bar_index = 100
        machine.previous_close = 99.0
        machine.true_ranges.extend([1.0] * 60)
        machine.history.extend(self._approach_bars())
        machine.pools.extend(
            [
                LiquidityPool(
                    pool_id="HIGH-SOURCE",
                    side="HIGH",
                    center=100.0,
                    lower=99.8,
                    upper=100.2,
                    event_time_ns=1,
                    observed_time_ns=2,
                    last_source_time_ns=1,
                    source_count=2,
                    max_prominence_atr=1.2,
                    status="ACTIVE",
                ),
                LiquidityPool(
                    pool_id="LOW-TARGET",
                    side="LOW",
                    center=90.0,
                    lower=89.8,
                    upper=90.2,
                    event_time_ns=1,
                    observed_time_ns=2,
                    last_source_time_ns=1,
                    source_count=2,
                    max_prominence_atr=1.1,
                    status="ACTIVE",
                ),
            ],
        )
        return machine

    def test_micro_pivot_confirms_reversal_that_stale_range_extreme_misses(self) -> None:
        full = self._machine(micro_pivot=True)
        ablation = self._machine(micro_pivot=False)
        sweep = BarView(200, 99.0, 101.0, 98.8, 99.5, 1.0)

        full_events = full._detect_sweep(sweep, 1.0)
        ablation_events = ablation._detect_sweep(sweep, 1.0)
        self.assertEqual(full.active.approach_level, 97.0)  # type: ignore[union-attr]
        self.assertEqual(ablation.active.approach_level, 90.0)  # type: ignore[union-attr]
        self.assertEqual(
            next(
                event.details["approach_structure_type"]
                for event in full_events
                if event.event_type == "LIQUIDITY_EVENT"
            ),
            "RIGHT_CONFIRMED_MICRO_PIVOT_LOW",
        )
        self.assertEqual(
            next(
                event.details["approach_structure_type"]
                for event in ablation_events
                if event.event_type == "LIQUIDITY_EVENT"
            ),
            "RANGE_EXTREME_ABLATION",
        )

        displacement = BarView(201, 99.4, 99.5, 95.5, 96.0, 1.0)
        full.bar_index += 1
        full_confirmations, full_plan = full._process_rejection(displacement, 1.0)
        ablation.bar_index += 1
        ablation_confirmations, ablation_plan = ablation._process_rejection(
            displacement,
            1.0,
        )
        self.assertIn(
            "DISPLACEMENT_CONFIRMED",
            [event.event_type for event in full_confirmations],
        )
        self.assertIsNotNone(full_plan)
        self.assertNotIn(
            "DISPLACEMENT_CONFIRMED",
            [event.event_type for event in ablation_confirmations],
        )
        self.assertIsNone(ablation_plan)


if __name__ == "__main__":
    unittest.main()
