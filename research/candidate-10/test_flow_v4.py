from __future__ import annotations

import unittest

from c10_flow_model import FlowBar
from c10_flow_model import FlowParams
from c10_flow_v4 import AcceptanceContinuationFlowAuctionStateMachine
from c10_flow_v4 import PriceOnlyAcceptanceContinuationStateMachine


def acceptance_bar(*, buyer: float, seller: float, macro_close: float = 30_020.0) -> FlowBar:
    return FlowBar(
        sequence=10,
        start_ns=1_000,
        end_ns=1_099,
        threshold_notional=buyer + seller,
        open=30_000.0,
        high=30_030.0,
        low=29_999.0,
        close=macro_close,
        quantity=(buyer + seller) / macro_close,
        notional=buyer + seller,
        buyer_notional=buyer,
        seller_notional=seller,
        path_travel=25.0,
        tick_count=20,
        previous_tick_price=macro_close,
    )


def features(*, macro_high: float = 30_200.0) -> dict[str, float]:
    return {
        "atr": 5.0,
        "delta_extreme": 0.20,
        "delta_reversal": 0.20,
        "absorption_efficiency": 0.50,
        "repricing_efficiency": 0.50,
        "range_high": 30_000.0,
        "range_low": 29_950.0,
        "macro_range_high": macro_high,
        "macro_range_low": 29_800.0,
    }


class AcceptanceContinuationTests(unittest.TestCase):
    def test_same_side_flow_acceptance_arms_boundary_retest(self) -> None:
        machine = AcceptanceContinuationFlowAuctionStateMachine(
            FlowParams(),
            tick_size=0.1,
            instrument_id="FULL",
        )
        events, plan = machine._detect_acceptance_continuation(
            acceptance_bar(buyer=850.0, seller=150.0),
            features(),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.direction, 1)
        self.assertEqual(plan.entry_price, 30_000.0)
        self.assertEqual(plan.target_price, 30_200.0)
        self.assertLess(plan.stop_price, plan.entry_price)
        self.assertGreaterEqual(
            float(plan.details["cost_adjusted_net_rr"]),
            machine.params.min_net_rr,
        )
        self.assertEqual(events[0].event_type, "ACCEPTANCE_CONFIRMED")
        self.assertEqual(events[0].observed_time_ns, 1_099)
        self.assertEqual(events[0].event_time_ns, 1_099)

    def test_acceptance_flow_is_exact_ablation(self) -> None:
        full = AcceptanceContinuationFlowAuctionStateMachine(
            FlowParams(),
            tick_size=0.1,
            instrument_id="FULL",
        )
        ablation = PriceOnlyAcceptanceContinuationStateMachine(
            FlowParams(),
            tick_size=0.1,
            instrument_id="ABLATION",
        )
        wrong_flow = acceptance_bar(buyer=150.0, seller=850.0)
        full_events, full_plan = full._detect_acceptance_continuation(
            wrong_flow,
            features(),
        )
        ablation_events, ablation_plan = ablation._detect_acceptance_continuation(
            wrong_flow,
            features(),
        )
        self.assertEqual(full_events, [])
        self.assertIsNone(full_plan)
        self.assertIsNotNone(ablation_plan)
        self.assertEqual(ablation_events[0].reason_code, "PRICE_EFFICIENCY_ACCEPTANCE_FLOW_ABLATION")

    def test_macro_target_must_cover_executable_cost(self) -> None:
        machine = AcceptanceContinuationFlowAuctionStateMachine(
            FlowParams(),
            tick_size=0.1,
            instrument_id="FULL",
        )
        events, plan = machine._detect_acceptance_continuation(
            acceptance_bar(buyer=850.0, seller=150.0),
            features(macro_high=30_025.0),
        )
        self.assertEqual(events, [])
        self.assertIsNone(plan)
        self.assertEqual(machine.counters["ACCEPTANCE_COST_RR_REJECTED"], 1)


if __name__ == "__main__":
    unittest.main()
