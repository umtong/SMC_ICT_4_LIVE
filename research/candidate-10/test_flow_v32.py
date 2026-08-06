from __future__ import annotations

from dataclasses import replace
import unittest

from c10_flow_model import FlowBar
from c10_flow_model import FlowParams
from c10_flow_model import FlowRaidProbe
from c10_flow_model import FlowTickView
from c10_flow_v31 import BoundaryRetestFlowAuctionStateMachine
from c10_flow_v32 import MultiscaleFlowAuctionStateMachine


def make_bar(
    sequence: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    buyer: float = 500.0,
    seller: float = 500.0,
    travel: float = 5.0,
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
        quantity=(buyer + seller) / close,
        notional=buyer + seller,
        buyer_notional=buyer,
        seller_notional=seller,
        path_travel=travel,
        tick_count=10,
        previous_tick_price=close,
    )


def probe(target: float) -> FlowRaidProbe:
    return FlowRaidProbe(
        scenario_id="TEST:FLOW:1",
        direction=-1,
        source_side="HIGH",
        boundary=30_000.0,
        opposite_boundary=target,
        raid_extreme=30_005.0,
        initiated_sequence=9,
        initiated_ns=999,
        initial_delta_ratio=0.8,
        initial_efficiency=0.1,
        initial_flow_threshold=0.5,
        initial_bar_open=29_995.0,
        initial_bar_close=29_995.0,
    )


class MultiscaleTargetTests(unittest.TestCase):
    def test_macro_target_passes_cost_gate_that_fast_target_fails(self) -> None:
        params = FlowParams()
        bar = make_bar(
            10,
            open_=30_000.0,
            high=30_000.0,
            low=29_980.0,
            close=29_980.0,
            buyer=200.0,
            seller=800.0,
            travel=20.0,
        )
        full = MultiscaleFlowAuctionStateMachine(
            params,
            tick_size=0.1,
            instrument_id="MACRO",
        )
        ablation = BoundaryRetestFlowAuctionStateMachine(
            params,
            tick_size=0.1,
            instrument_id="FAST",
        )
        full_plan = full._build_plan(bar, probe(29_800.0), {"atr": 5.0})
        ablation_plan = ablation._build_plan(bar, probe(29_970.0), {"atr": 5.0})
        self.assertIsNotNone(full_plan)
        self.assertIsNone(ablation_plan)
        assert full_plan is not None
        self.assertGreaterEqual(
            float(full_plan.details["cost_adjusted_net_rr"]),
            params.min_net_rr,
        )

    def test_absorption_probe_receives_prior_macro_opposite_boundary(self) -> None:
        params = replace(
            FlowParams(),
            minimum_feature_history=8,
            minimum_atr_history=8,
            feature_lookback=32,
            atr_event_bars=16,
            range_event_bars=8,
        )
        machine = MultiscaleFlowAuctionStateMachine(
            params,
            tick_size=0.1,
            instrument_id="TEST",
        )
        machine.true_ranges.extend([5.0] * 16)
        machine.abs_delta_history.extend([0.20] * 16)
        machine.efficiency_history.extend([0.50] * 16)
        for sequence in range(8):
            machine.completed_bars.append(
                make_bar(
                    sequence,
                    open_=29_985.0,
                    high=30_000.0,
                    low=29_970.0,
                    close=29_985.0,
                ),
            )
            machine.macro_completed_bars.append(
                make_bar(
                    sequence,
                    open_=30_000.0,
                    high=30_100.0,
                    low=29_800.0,
                    close=30_000.0,
                ),
            )
        features = machine._feature_snapshot()
        self.assertIsNotNone(features)
        assert features is not None
        raid = make_bar(
            8,
            open_=29_999.0,
            high=30_002.0,
            low=29_998.0,
            close=29_999.0,
            buyer=850.0,
            seller=150.0,
            travel=5.0,
        )
        events = machine._detect_absorption_raid(raid, features)
        self.assertIsNotNone(machine.active_probe)
        assert machine.active_probe is not None
        self.assertEqual(machine.active_probe.opposite_boundary, 29_800.0)
        event = events[0]
        self.assertEqual(event.details["fine_opposite_boundary"], 29_970.0)
        self.assertEqual(
            event.details["target_scale"],
            "MACRO_EVENT_NOTIONAL_AUCTION",
        )

    def test_macro_bar_closing_on_signal_tick_is_not_used_as_target(self) -> None:
        class InspectMachine(MultiscaleFlowAuctionStateMachine):
            seen_macro_count: int | None = None

            def _on_completed_bar(self, bar: FlowBar):  # type: ignore[override]
                self.seen_macro_count = len(self.macro_completed_bars)
                return [], None

        params = replace(
            FlowParams(),
            minimum_minute_history=1,
            minute_notional_lookback=1,
            event_notional_fraction=0.25,
        )
        machine = InspectMachine(
            params,
            tick_size=0.1,
            instrument_id="CAUSAL",
        )
        machine.minute_history.append(1_000.0)
        tick = FlowTickView(
            ts_ns=10_000,
            price=100.0,
            quantity=10.0,
            aggressor=1,
            trade_id="1",
        )
        _, _, completed = machine.on_tick(tick)
        self.assertIsNotNone(completed)
        self.assertEqual(machine.seen_macro_count, 0)
        self.assertEqual(len(machine.macro_completed_bars), 1)


if __name__ == "__main__":
    unittest.main()
