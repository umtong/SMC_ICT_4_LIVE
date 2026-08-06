from __future__ import annotations

from dataclasses import replace
import unittest

from c10_flow_model import FlowBar
from c10_flow_model import FlowParams
from c10_flow_model import FlowRaidProbe
from c10_flow_v31 import BoundaryRetestFlowAuctionStateMachine
from c10_flow_v31 import MidpointFlowAuctionStateMachine


def repricing_bar() -> FlowBar:
    return FlowBar(
        sequence=10,
        start_ns=1_000,
        end_ns=1_099,
        threshold_notional=1_000.0,
        open=100.0,
        high=100.0,
        low=98.0,
        close=98.0,
        quantity=10.0,
        notional=1_000.0,
        buyer_notional=100.0,
        seller_notional=900.0,
        path_travel=2.0,
        tick_count=10,
        previous_tick_price=98.0,
    )


def high_probe() -> FlowRaidProbe:
    return FlowRaidProbe(
        scenario_id="TEST:FLOW:1",
        direction=-1,
        source_side="HIGH",
        boundary=100.0,
        opposite_boundary=95.0,
        raid_extreme=101.0,
        initiated_sequence=9,
        initiated_ns=999,
        initial_delta_ratio=0.8,
        initial_efficiency=0.1,
        initial_flow_threshold=0.5,
        initial_bar_open=99.0,
        initial_bar_close=99.0,
    )


class BoundaryEntryTests(unittest.TestCase):
    def test_boundary_entry_passes_where_midpoint_fails_same_rr_gate(self) -> None:
        params = replace(
            FlowParams(),
            maker_fee=0.0,
            taker_fee=0.0,
            execution_reserve_ticks=0,
            stop_buffer_atr=1.0,
            min_net_rr=2.0,
        )
        features = {"atr": 1.0}
        boundary = BoundaryRetestFlowAuctionStateMachine(
            params,
            tick_size=0.1,
            instrument_id="BOUNDARY",
        )
        midpoint = MidpointFlowAuctionStateMachine(
            params,
            tick_size=0.1,
            instrument_id="MIDPOINT",
        )
        full_plan = boundary._build_plan(repricing_bar(), high_probe(), features)
        ablation_plan = midpoint._build_plan(repricing_bar(), high_probe(), features)
        self.assertIsNotNone(full_plan)
        self.assertIsNone(ablation_plan)
        assert full_plan is not None
        self.assertEqual(full_plan.entry_price, 100.0)
        self.assertEqual(full_plan.details["entry_anchor"], "ABSORBED_SOURCE_BOUNDARY")
        self.assertEqual(full_plan.details["cost_adjusted_net_rr"], 2.5)

    def test_boundary_order_is_passive_after_repricing(self) -> None:
        machine = BoundaryRetestFlowAuctionStateMachine(
            replace(FlowParams(), maker_fee=0.0, taker_fee=0.0, min_net_rr=0.1),
            tick_size=0.1,
            instrument_id="TEST",
        )
        plan = machine._build_plan(
            repricing_bar(),
            high_probe(),
            {"atr": 1.0},
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertGreater(plan.entry_price, repricing_bar().close)
        self.assertGreater(plan.stop_price, plan.entry_price)
        self.assertLess(plan.target_price, plan.entry_price)


if __name__ == "__main__":
    unittest.main()
