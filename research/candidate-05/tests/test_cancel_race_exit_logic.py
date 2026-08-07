from __future__ import annotations

import unittest

from cancel_race_exit_logic import NO_ACTION
from cancel_race_exit_logic import SUBMIT_MARKET_FLATTEN
from cancel_race_exit_logic import WAIT_FOR_CONTINGENT_RESOLUTION
from cancel_race_exit_logic import cancel_race_exit_action


class CancelRaceExitLogicTest(unittest.TestCase):
    def test_existing_structural_exit_must_resolve_before_market_flatten(self) -> None:
        self.assertEqual(
            cancel_race_exit_action(
                position_open=True,
                open_reduce_only_orders=True,
                flatten_submitted=False,
            ),
            WAIT_FOR_CONTINGENT_RESOLUTION,
        )

    def test_flatten_is_submitted_once_after_contingents_are_gone(self) -> None:
        self.assertEqual(
            cancel_race_exit_action(
                position_open=True,
                open_reduce_only_orders=False,
                flatten_submitted=False,
            ),
            SUBMIT_MARKET_FLATTEN,
        )
        self.assertEqual(
            cancel_race_exit_action(
                position_open=True,
                open_reduce_only_orders=False,
                flatten_submitted=True,
            ),
            NO_ACTION,
        )

    def test_position_already_closed_never_submits_duplicate_reduce_only_exit(self) -> None:
        self.assertEqual(
            cancel_race_exit_action(
                position_open=False,
                open_reduce_only_orders=False,
                flatten_submitted=False,
            ),
            NO_ACTION,
        )


if __name__ == "__main__":
    unittest.main()
