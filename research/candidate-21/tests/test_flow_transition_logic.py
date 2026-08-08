from __future__ import annotations

import unittest

from flow_transition_logic import FlowTransitionEvidence
from flow_transition_logic import FlowTransitionRoute
from flow_transition_logic import classify_flow_transition
from flow_transition_logic import evidence_from_router_details


def _evidence(
    *,
    direction: int = 1,
    event_open: float = 100.0,
    event_high: float = 102.0,
    event_low: float = 99.0,
    event_close: float = 101.5,
    event_flow: float = 0.70,
    response_flow: float = 0.80,
    response_return_bps: float = 1.0,
    response_efficiency: float = 0.80,
) -> FlowTransitionEvidence:
    return FlowTransitionEvidence(
        direction=direction,
        event_open=event_open,
        event_high=event_high,
        event_low=event_low,
        event_close=event_close,
        event_flow=event_flow,
        response_flow=response_flow,
        response_return_bps=response_return_bps,
        response_efficiency=response_efficiency,
    )


class FlowTransitionLogicTests(unittest.TestCase):
    def test_persistent_sponsorship_uses_relational_flow(self) -> None:
        decision = classify_flow_transition(_evidence())
        self.assertTrue(decision.eligible)
        self.assertIs(
            decision.route,
            FlowTransitionRoute.PERSISTENT_SPONSORSHIP,
        )
        self.assertGreaterEqual(
            decision.evidence.directional_response_flow,
            decision.evidence.directional_event_flow,
        )

    def test_delayed_price_discovery_can_replace_flow_decay(self) -> None:
        evidence = _evidence(
            event_open=100.0,
            event_high=102.0,
            event_low=98.0,
            event_close=100.1,
            event_flow=0.60,
            response_flow=0.50,
            response_return_bps=20.0,
            response_efficiency=0.90,
        )
        decision = classify_flow_transition(evidence)
        self.assertTrue(decision.eligible)
        self.assertIs(
            decision.route,
            FlowTransitionRoute.DELAYED_PRICE_DISCOVERY,
        )
        self.assertGreater(
            evidence.response_efficiency,
            evidence.event_efficiency,
        )
        self.assertGreater(
            evidence.directional_response_return_bps,
            evidence.directional_event_return_bps,
        )

    def test_flow_and_efficiency_decay_closes_no_trade(self) -> None:
        evidence = _evidence(
            event_open=100.0,
            event_high=102.0,
            event_low=99.0,
            event_close=101.8,
            event_flow=0.80,
            response_flow=0.35,
            response_return_bps=2.0,
            response_efficiency=0.50,
        )
        decision = classify_flow_transition(evidence)
        self.assertFalse(decision.eligible)
        self.assertIs(
            decision.route,
            FlowTransitionRoute.DECAYED_NO_TRADE,
        )
        self.assertIn("DECAYED", decision.reason)

    def test_same_side_flow_is_required_even_with_efficiency(self) -> None:
        evidence = _evidence(
            event_open=100.0,
            event_high=102.0,
            event_low=98.0,
            event_close=100.1,
            event_flow=0.40,
            response_flow=-0.10,
            response_return_bps=50.0,
            response_efficiency=1.0,
        )
        decision = classify_flow_transition(evidence)
        self.assertFalse(decision.eligible)
        self.assertIn("LOST_SAME_SIDE", decision.reason)

    def test_short_side_is_symmetric(self) -> None:
        evidence = _evidence(
            direction=-1,
            event_open=101.0,
            event_high=102.0,
            event_low=99.0,
            event_close=99.5,
            event_flow=-0.70,
            response_flow=-0.80,
            response_return_bps=-1.0,
            response_efficiency=0.80,
        )
        decision = classify_flow_transition(evidence)
        self.assertTrue(decision.eligible)
        self.assertIs(
            decision.route,
            FlowTransitionRoute.PERSISTENT_SPONSORSHIP,
        )

    def test_router_details_are_frozen_without_outcome(self) -> None:
        raw = {
            "event": {
                "direction": 1,
                "event_open": 100.0,
                "event_high": 102.0,
                "event_low": 99.0,
                "event_close": 101.5,
                "event_flow": 0.70,
            },
            "response": {
                "flow": 0.80,
                "return_bps": 1.0,
                "efficiency": 0.80,
            },
            "acceptance_target_touched": False,
            "rejection_target_touched": False,
        }
        evidence = evidence_from_router_details(raw)
        self.assertEqual(evidence.direction, 1)
        self.assertAlmostEqual(evidence.event_flow, 0.70)
        self.assertAlmostEqual(evidence.response_flow, 0.80)
        decision = classify_flow_transition(evidence)
        details = decision.details()
        lowered = {key.lower() for key in details}
        self.assertNotIn("pnl", lowered)
        self.assertNotIn("outcome", lowered)

    def test_incomplete_router_details_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            evidence_from_router_details({"event": {}, "response": {}})


if __name__ == "__main__":
    unittest.main()
