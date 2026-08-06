from __future__ import annotations

import unittest

from entry_cancel_race_logic import entry_cancel_resolution


class EntryCancelRaceLogicTest(unittest.TestCase):
    def test_state_is_not_closed_at_cancel_request(self) -> None:
        self.assertEqual(
            entry_cancel_resolution(
                cancel_requested=True,
                cancel_confirmed=False,
                position_open=False,
                bar_index=10,
                requested_index=10,
            ),
            "WAIT",
        )

    def test_fill_wins_over_cancel_confirmation(self) -> None:
        self.assertEqual(
            entry_cancel_resolution(
                cancel_requested=True,
                cancel_confirmed=True,
                position_open=True,
                bar_index=10,
                requested_index=10,
            ),
            "FLATTEN_FILLED_ENTRY",
        )

    def test_unfilled_close_requires_later_bar_and_cancel_confirmation(self) -> None:
        self.assertEqual(
            entry_cancel_resolution(
                cancel_requested=True,
                cancel_confirmed=True,
                position_open=False,
                bar_index=10,
                requested_index=10,
            ),
            "WAIT",
        )
        self.assertEqual(
            entry_cancel_resolution(
                cancel_requested=True,
                cancel_confirmed=True,
                position_open=False,
                bar_index=11,
                requested_index=10,
            ),
            "CLOSE_UNFILLED",
        )

    def test_inactive_state_is_explicit(self) -> None:
        self.assertEqual(
            entry_cancel_resolution(
                cancel_requested=False,
                cancel_confirmed=False,
                position_open=False,
                bar_index=10,
                requested_index=-1,
            ),
            "INACTIVE",
        )


if __name__ == "__main__":
    unittest.main()
