from __future__ import annotations

import unittest

from nt_liquidity_strategy_v5 import classify_session_sweep


class SessionAuctionRouterTests(unittest.TestCase):
    def test_shallow_penetration_is_rejection_state(self) -> None:
        self.assertEqual(
            classify_session_sweep(0.45, 1.0),
            "SHALLOW_REJECTION",
        )

    def test_deep_penetration_requires_strong_participation(self) -> None:
        self.assertEqual(
            classify_session_sweep(0.75, 3.0),
            "DEEP_PRICE_DISCOVERY_PROBE",
        )
        self.assertEqual(
            classify_session_sweep(0.75, 2.99),
            "AMBIGUOUS_SKIP",
        )

    def test_middle_penetration_is_not_forced_into_a_trade(self) -> None:
        self.assertEqual(
            classify_session_sweep(0.60, 10.0),
            "AMBIGUOUS_SKIP",
        )


if __name__ == "__main__":
    unittest.main()
