import math
import unittest

from boundary_response_router import (
    BoundaryResponse,
    BoundaryResponseDecision,
    classify_boundary_response,
)


def response(direction=1, **overrides):
    values = dict(
        direction=direction,
        boundary_level=100.0,
        opening_close=101.0 if direction > 0 else 99.0,
        minute_close=102.0 if direction > 0 else 98.0,
        minute_notional=1000.0,
        minute_flow=0.4 * direction,
        opening_notional=200.0,
        opening_signed_notional=100.0 * direction,
        depth_imbalance_1=0.2 * direction,
        liquidity_ahead_change_1m=-0.1,
    )
    values.update(overrides)
    return BoundaryResponse(**values)


class BoundaryResponseRouterTest(unittest.TestCase):
    def test_long_and_short_acceptance_are_mirror_symmetric(self):
        for direction in (1, -1):
            decision, _, details = classify_boundary_response(response(direction))
            self.assertEqual(decision, BoundaryResponseDecision.ACCEPTANCE)
            self.assertGreater(details["directional_response_flow"], 0.0)
            self.assertGreater(details["directional_response_return"], 0.0)
            self.assertGreater(details["directional_outside_close"], 0.0)

    def test_failed_auction_uses_strictly_later_reversal_and_refill(self):
        value = response(
            1,
            minute_close=99.0,
            minute_flow=-0.2,
            depth_imbalance_1=-0.2,
            liquidity_ahead_change_1m=0.1,
        )
        decision, reason, details = classify_boundary_response(value)
        self.assertEqual(decision, BoundaryResponseDecision.FAILED_AUCTION)
        self.assertIn("REVERSED", reason)
        self.assertLess(details["directional_response_flow"], 0.0)

    def test_opening_aggression_is_removed_from_response_flow(self):
        value = response(
            1,
            minute_close=99.0,
            minute_notional=1000.0,
            minute_flow=0.10,
            opening_notional=400.0,
            opening_signed_notional=300.0,
            depth_imbalance_1=-0.2,
            liquidity_ahead_change_1m=0.1,
        )
        decision, _, details = classify_boundary_response(value)
        self.assertEqual(decision, BoundaryResponseDecision.FAILED_AUCTION)
        self.assertGreater(value.minute_flow, 0.0)
        self.assertLess(details["response_flow"], 0.0)

    def test_mixed_price_and_book_is_unresolved(self):
        decision, _, _ = classify_boundary_response(
            response(1, depth_imbalance_1=-0.2),
        )
        self.assertEqual(decision, BoundaryResponseDecision.UNRESOLVED)

    def test_no_later_traded_notional_is_unresolved(self):
        value = response(
            1,
            minute_notional=200.0,
            minute_flow=0.5,
            opening_notional=200.0,
            opening_signed_notional=100.0,
        )
        decision, reason, details = classify_boundary_response(value)
        self.assertEqual(decision, BoundaryResponseDecision.UNRESOLVED)
        self.assertEqual(reason, "NO_STRICTLY_LATER_TRADED_RESPONSE")
        self.assertTrue(math.isnan(details["response_flow"]))


if __name__ == "__main__":
    unittest.main()
