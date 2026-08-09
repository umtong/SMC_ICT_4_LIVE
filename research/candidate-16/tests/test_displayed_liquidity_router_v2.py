from __future__ import annotations

import unittest

from displayed_liquidity_router import FailureLeg
from displayed_liquidity_router import InitiativeDecision
from displayed_liquidity_router import InitiativeObservation
from displayed_liquidity_router import advance_failure_leg
from displayed_liquidity_router import displayed_acceptance_supported
from displayed_liquidity_router import displayed_failure_supported


class DisplayedLiquidityRouterTests(unittest.TestCase):
    def test_failed_auction_needs_book_support_and_refill(self) -> None:
        self.assertTrue(
            displayed_failure_supported(
                parent_direction=-1,
                max_reversal_book_support=0.01,
                max_defending_depth_change=0.02,
            ),
        )
        self.assertFalse(
            displayed_failure_supported(
                parent_direction=-1,
                max_reversal_book_support=-0.01,
                max_defending_depth_change=0.02,
            ),
        )
        self.assertFalse(
            displayed_failure_supported(
                parent_direction=-1,
                max_reversal_book_support=0.01,
                max_defending_depth_change=-0.02,
            ),
        )

    def test_acceptance_needs_directional_book_and_withdrawal_ahead(self) -> None:
        self.assertTrue(
            displayed_acceptance_supported(
                parent_direction=1,
                max_acceptance_book_support=0.01,
                min_liquidity_ahead_change=-0.01,
            ),
        )
        self.assertFalse(
            displayed_acceptance_supported(
                parent_direction=1,
                max_acceptance_book_support=0.01,
                min_liquidity_ahead_change=0.0,
            ),
        )

    @staticmethod
    def _long_state() -> FailureLeg:
        return FailureLeg(
            scenario_id="x",
            side=1,
            failure_index=10,
            last_index=10,
            failure_high=101.0,
            failure_low=99.0,
            parent_extreme=98.0,
            max_wait_bars=3,
        )

    def test_failure_bar_cannot_confirm_itself(self) -> None:
        with self.assertRaises(ValueError):
            advance_failure_leg(
                self._long_state(),
                InitiativeObservation(10, 102.0, 99.0, 101.5, 0.2, 4.0, 0.1, -0.1),
            )

    def test_later_initiative_requires_price_flow_book_and_withdrawal(self) -> None:
        confirmed = advance_failure_leg(
            self._long_state(),
            InitiativeObservation(11, 102.0, 99.2, 101.5, 0.2, 4.0, 0.1, -0.1),
        )
        self.assertEqual(confirmed.decision, InitiativeDecision.CONFIRMED)

        no_withdrawal = advance_failure_leg(
            self._long_state(),
            InitiativeObservation(11, 102.0, 99.2, 101.5, 0.2, 4.0, 0.1, 0.1),
        )
        self.assertEqual(no_withdrawal.decision, InitiativeDecision.WAITING)

    def test_parent_extreme_reaccess_invalidates_before_confirmation(self) -> None:
        invalid = advance_failure_leg(
            self._long_state(),
            InitiativeObservation(11, 102.0, 97.9, 101.5, 0.2, 4.0, 0.1, -0.1),
        )
        self.assertEqual(invalid.decision, InitiativeDecision.INVALIDATED)

    def test_expiry_is_terminal(self) -> None:
        state = self._long_state()
        for index in (11, 12, 13):
            state = advance_failure_leg(
                state,
                InitiativeObservation(index, 100.8, 99.2, 100.0, 0.2, 1.0, 0.1, -0.1),
            )
        self.assertEqual(state.decision, InitiativeDecision.EXPIRED)
        with self.assertRaises(ValueError):
            advance_failure_leg(
                state,
                InitiativeObservation(14, 102.0, 99.0, 101.5, 0.2, 4.0, 0.1, -0.1),
            )

    def test_short_side_is_mirror_symmetric(self) -> None:
        state = FailureLeg(
            scenario_id="short",
            side=-1,
            failure_index=20,
            last_index=20,
            failure_high=101.0,
            failure_low=99.0,
            parent_extreme=102.0,
        )
        confirmed = advance_failure_leg(
            state,
            InitiativeObservation(21, 100.8, 98.0, 98.5, -0.2, -4.0, -0.1, -0.1),
        )
        self.assertEqual(confirmed.decision, InitiativeDecision.CONFIRMED)


if __name__ == "__main__":
    unittest.main()
