from __future__ import annotations

import unittest

from nt_opening_auction_strategy import NANOS_PER_MINUTE
from nt_opening_auction_strategy import SESSION_MINUTES
from nt_opening_auction_strategy import session_coordinates


class OpeningAuctionClockTests(unittest.TestCase):
    def test_session_resets_every_eight_hours(self) -> None:
        self.assertEqual(session_coordinates(0), (0, 0))
        self.assertEqual(
            session_coordinates((SESSION_MINUTES - 1) * NANOS_PER_MINUTE),
            (0, SESSION_MINUTES - 1),
        )
        self.assertEqual(
            session_coordinates(SESSION_MINUTES * NANOS_PER_MINUTE),
            (1, 0),
        )

    def test_bar_close_inside_minute_keeps_correct_offset(self) -> None:
        ts = 31 * NANOS_PER_MINUTE + 59_999_000_000
        self.assertEqual(session_coordinates(ts), (0, 31))


if __name__ == "__main__":
    unittest.main()
