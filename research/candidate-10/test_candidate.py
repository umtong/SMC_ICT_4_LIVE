"""Pure causal-state tests; execution integration is covered by the workflow run."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import unittest

from candidate import AuctionStateMachine
from candidate import BarView
from candidate import LiquidityPool
from candidate import MachineParams
from candidate import StructuralBar
from candidate import reproducible_weeks


class Candidate10Tests(unittest.TestCase):
    def test_week_selection_is_stable(self) -> None:
        self.assertEqual(
            reproducible_weeks(),
            [date(2023, 10, 16), date(2023, 5, 15), date(2024, 1, 15)],
        )

    def test_pivot_is_not_known_until_right_structure_bar_closes(self) -> None:
        params = replace(
            MachineParams(),
            structure_minutes=5,
            pivot_left=1,
            pivot_right=1,
            structural_atr_lookback=10,
        )
        machine = AuctionStateMachine(params, tick_size=0.1, instrument_id="TEST")
        machine.structural_true_ranges.extend([5.0] * 10)
        bars = [
            StructuralBar(1, 0, 300, 100.0, 102.0, 98.0, 100.0, 1.0, 5),
            StructuralBar(2, 300, 600, 100.0, 110.0, 99.0, 101.0, 1.0, 5),
            StructuralBar(3, 600, 900, 101.0, 103.0, 97.0, 99.0, 1.0, 5),
        ]
        first = machine._finalize_structural(bars[0], observed_time_ns=300)
        second = machine._finalize_structural(bars[1], observed_time_ns=600)
        third = machine._finalize_structural(bars[2], observed_time_ns=900)
        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(len(third), 1)
        event = third[0]
        self.assertEqual(event.event_type, "POOL_CONFIRMED")
        self.assertEqual(event.event_time_ns, 600)
        self.assertEqual(event.observed_time_ns, 900)
        self.assertGreater(event.observed_time_ns, event.event_time_ns)

    def test_equal_level_cluster_activates_without_prominent_single(self) -> None:
        params = replace(
            MachineParams(),
            single_swing_prominence_atr=2.0,
            cluster_min_sources=2,
            enable_pool_clustering=True,
        )
        machine = AuctionStateMachine(params, tick_size=0.1, instrument_id="TEST")
        first = machine._upsert_pool(
            side="HIGH",
            price=100.0,
            prominence_atr=0.5,
            event_time_ns=1,
            observed_time_ns=2,
            structural_atr=10.0,
        )
        second = machine._upsert_pool(
            side="HIGH",
            price=100.5,
            prominence_atr=0.6,
            event_time_ns=3,
            observed_time_ns=4,
            structural_atr=10.0,
        )
        self.assertEqual(first.next_state, "LATENT")
        self.assertEqual(second.event_type, "POOL_CLUSTERED")
        self.assertEqual(second.next_state, "ACTIVE")
        self.assertEqual(machine.pools[0].source_count, 2)

        ablated = AuctionStateMachine(
            replace(params, enable_pool_clustering=False),
            tick_size=0.1,
            instrument_id="TEST-ABLATION",
        )
        ablated._upsert_pool(
            side="HIGH",
            price=100.0,
            prominence_atr=0.5,
            event_time_ns=1,
            observed_time_ns=2,
            structural_atr=10.0,
        )
        event = ablated._upsert_pool(
            side="HIGH",
            price=100.5,
            prominence_atr=0.6,
            event_time_ns=3,
            observed_time_ns=4,
            structural_atr=10.0,
        )
        self.assertEqual(event.next_state, "LATENT")

    def test_pool_cannot_be_swept_on_its_observation_bar(self) -> None:
        machine = AuctionStateMachine(MachineParams(), tick_size=0.1, instrument_id="TEST")
        machine.previous_close = 99.0
        machine.true_ranges.extend([1.0] * 60)
        machine.history.extend(
            BarView(i, 99.0, 99.5, 98.5, 99.0, 1.0) for i in range(8)
        )
        machine.pools.append(
            LiquidityPool(
                pool_id="P1",
                side="HIGH",
                center=100.0,
                lower=99.8,
                upper=100.2,
                event_time_ns=50,
                observed_time_ns=100,
                last_source_time_ns=50,
                source_count=2,
                max_prominence_atr=1.0,
                status="ACTIVE",
            ),
        )
        events = machine._detect_sweep(
            BarView(100, 99.0, 101.0, 98.8, 99.5, 1.0),
            1.0,
        )
        self.assertEqual(events, [])
        self.assertIsNone(machine.active)

    def test_confirmed_pool_sweep_displacement_arms_pool_to_pool_limit(self) -> None:
        params = replace(
            MachineParams(),
            maker_fee=0.0,
            taker_fee=0.0,
            min_net_rr=0.5,
            stop_buffer_atr=0.5,
        )
        machine = AuctionStateMachine(params, tick_size=0.1, instrument_id="TEST")
        machine.bar_index = 100
        machine.previous_close = 100.0
        machine.true_ranges.extend([1.0] * 60)
        machine.history.extend(
            BarView(i, 100.0, 100.5, 99.5, 100.0, 1.0) for i in range(8)
        )
        source = LiquidityPool(
            pool_id="LOW-SOURCE",
            side="LOW",
            center=95.0,
            lower=94.8,
            upper=95.2,
            event_time_ns=1,
            observed_time_ns=2,
            last_source_time_ns=1,
            source_count=2,
            max_prominence_atr=1.2,
            status="ACTIVE",
        )
        target = LiquidityPool(
            pool_id="HIGH-TARGET",
            side="HIGH",
            center=105.0,
            lower=104.8,
            upper=105.2,
            event_time_ns=1,
            observed_time_ns=2,
            last_source_time_ns=1,
            source_count=2,
            max_prominence_atr=1.1,
            status="ACTIVE",
        )
        machine.pools.extend([source, target])

        raid = BarView(200, 96.0, 96.2, 94.5, 95.5, 1.0)
        events = machine._detect_sweep(raid, 1.0)
        self.assertIn("LIQUIDITY_EVENT", [item.event_type for item in events])
        self.assertEqual(source.status, "CONSUMED")
        self.assertIsNotNone(machine.active)

        machine.bar_index += 1
        displacement = BarView(201, 95.5, 102.0, 95.4, 101.8, 1.0)
        events, plan = machine._process_rejection(displacement, 1.0)
        self.assertIn("DISPLACEMENT_CONFIRMED", [item.event_type for item in events])
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.entry_order_type, "LIMIT")
        self.assertEqual(plan.details["source_pool_id"], "LOW-SOURCE")
        self.assertEqual(plan.details["target_pool_id"], "HIGH-TARGET")
        self.assertGreater(plan.target_price, plan.entry_estimate)
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
