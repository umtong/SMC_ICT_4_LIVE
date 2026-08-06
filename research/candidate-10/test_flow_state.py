from __future__ import annotations

from dataclasses import replace
import unittest

from c10_flow_model import FlowBar
from c10_flow_model import FlowParams
from c10_flow_model import FlowTickView
from c10_flow_state import FlowAuctionStateMachine


def make_bar(
    sequence: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    buyer: float,
    seller: float,
    travel: float,
) -> FlowBar:
    return FlowBar(
        sequence=sequence,
        start_ns=sequence * 100,
        end_ns=sequence * 100 + 99,
        threshold_notional=buyer + seller,
        open=open_,
        high=high,
        low=low,
        close=close,
        quantity=(buyer + seller) / max(close, 1.0),
        notional=buyer + seller,
        buyer_notional=buyer,
        seller_notional=seller,
        path_travel=travel,
        tick_count=10,
        previous_tick_price=close,
    )


def seeded_machine(
    *,
    order_flow: bool = True,
    range_low: float = 90.0,
) -> FlowAuctionStateMachine:
    params = replace(
        FlowParams(),
        enable_order_flow=order_flow,
        minimum_feature_history=8,
        minimum_atr_history=8,
        feature_lookback=32,
        atr_event_bars=16,
        range_event_bars=8,
        min_net_rr=1.35,
    )
    machine = FlowAuctionStateMachine(
        params,
        tick_size=0.1,
        instrument_id="TEST",
    )
    machine.true_ranges.extend([2.0] * 16)
    machine.abs_delta_history.extend([0.20] * 16)
    machine.efficiency_history.extend([0.50] * 16)
    for sequence in range(8):
        machine.completed_bars.append(
            make_bar(
                sequence,
                open_=95.0,
                high=100.0,
                low=range_low,
                close=95.0,
                buyer=500.0,
                seller=500.0,
                travel=5.0,
            ),
        )
    machine.next_sequence = 8
    machine.previous_flow_close = 95.0
    return machine


class FlowStateTests(unittest.TestCase):
    def test_event_threshold_uses_only_completed_minutes(self) -> None:
        params = replace(
            FlowParams(),
            minimum_minute_history=3,
            minute_notional_lookback=3,
            event_notional_fraction=0.25,
        )
        machine = FlowAuctionStateMachine(
            params,
            tick_size=0.1,
            instrument_id="TEST",
        )
        machine.minute_history.extend([100.0, 100.0, 100.0])
        tick = FlowTickView(
            ts_ns=10 * 60_000_000_000,
            price=100.0,
            quantity=10.0,
            aggressor=1,
            trade_id="1",
        )
        _, _, completed = machine.on_tick(tick)
        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.threshold_notional, 25.0)
        self.assertEqual(completed.notional, 1000.0)

    def test_absorption_then_opposite_repricing_creates_cost_qualified_plan(self) -> None:
        machine = seeded_machine()
        raid = make_bar(
            8,
            open_=99.5,
            high=101.0,
            low=98.0,
            close=98.5,
            buyer=800.0,
            seller=200.0,
            travel=4.0,
        )
        events, plan = machine._on_completed_bar(raid)
        self.assertIsNone(plan)
        self.assertIn("ABSORPTION_PROBED", [event.event_type for event in events])
        self.assertIsNotNone(machine.active_probe)

        repricing = make_bar(
            9,
            open_=98.5,
            high=98.7,
            low=97.3,
            close=97.5,
            buyer=200.0,
            seller=800.0,
            travel=1.5,
        )
        events, plan = machine._on_completed_bar(repricing)
        self.assertIn("REPRICING_CONFIRMED", [event.event_type for event in events])
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.direction, -1)
        self.assertLess(plan.target_price, plan.entry_price)
        self.assertGreater(plan.stop_price, plan.entry_price)
        self.assertGreaterEqual(
            float(plan.details["cost_adjusted_net_rr"]),
            machine.params.min_net_rr,
        )
        self.assertEqual(events[-1].observed_time_ns, repricing.end_ns)

    def test_order_flow_filter_is_exact_core_ablation(self) -> None:
        full = seeded_machine(order_flow=True)
        ablation = seeded_machine(order_flow=False)
        wrong_signed_raid = make_bar(
            8,
            open_=99.5,
            high=101.0,
            low=98.0,
            close=98.5,
            buyer=200.0,
            seller=800.0,
            travel=4.0,
        )
        full_events, _ = full._on_completed_bar(wrong_signed_raid)
        ablation_events, _ = ablation._on_completed_bar(wrong_signed_raid)
        self.assertNotIn(
            "ABSORPTION_PROBED",
            [event.event_type for event in full_events],
        )
        self.assertIn(
            "ABSORPTION_PROBED",
            [event.event_type for event in ablation_events],
        )

    def test_efficient_price_response_is_not_labeled_absorption(self) -> None:
        machine = seeded_machine()
        efficient_raid = make_bar(
            8,
            open_=98.0,
            high=101.0,
            low=97.8,
            close=99.8,
            buyer=800.0,
            seller=200.0,
            travel=2.0,
        )
        events, plan = machine._on_completed_bar(efficient_raid)
        self.assertIsNone(plan)
        self.assertEqual(events, [])
        self.assertIsNone(machine.active_probe)
        self.assertEqual(
            machine.counters["PRICE_RESPONSE_NOT_ABSORPTIVE"],
            1,
        )

    def test_opposite_boundary_that_fails_cost_rr_is_rejected(self) -> None:
        machine = seeded_machine(range_low=96.0)
        raid = make_bar(
            8,
            open_=99.5,
            high=101.0,
            low=98.0,
            close=98.5,
            buyer=800.0,
            seller=200.0,
            travel=4.0,
        )
        machine._on_completed_bar(raid)
        repricing = make_bar(
            9,
            open_=98.5,
            high=98.7,
            low=97.3,
            close=97.5,
            buyer=200.0,
            seller=800.0,
            travel=1.5,
        )
        events, plan = machine._on_completed_bar(repricing)
        self.assertIsNone(plan)
        self.assertIn("SCENARIO_INVALIDATED", [event.event_type for event in events])
        self.assertEqual(
            events[-1].reason_code,
            "OPPOSITE_BOUNDARY_FAILS_COST_ADJUSTED_RR",
        )


if __name__ == "__main__":
    unittest.main()
