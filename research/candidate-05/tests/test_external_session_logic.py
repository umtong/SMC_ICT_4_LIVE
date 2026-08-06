from __future__ import annotations

from datetime import datetime, timezone
import unittest

from external_session_logic import utc_session_key
from external_session_logic import validate_uniform_session_hours


HOURS = (0, 4, 8, 12, 16, 20)


def ns(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(
        datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()
        * 1_000_000_000
    )


class ExternalSessionLogicTest(unittest.TestCase):
    def test_fixed_clock_is_uniform_four_hours(self) -> None:
        self.assertEqual(validate_uniform_session_hours(HOURS), 4)

    def test_key_changes_only_at_completed_session_boundary(self) -> None:
        self.assertEqual(utc_session_key(ns(2024, 1, 2, 3, 59), HOURS), 2024010200)
        self.assertEqual(utc_session_key(ns(2024, 1, 2, 4, 0), HOURS), 2024010204)
        self.assertEqual(utc_session_key(ns(2024, 1, 2, 23, 59), HOURS), 2024010220)
        self.assertEqual(utc_session_key(ns(2024, 1, 3, 0, 0), HOURS), 2024010300)

    def test_invalid_clock_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_uniform_session_hours((0, 4, 9, 12, 16, 20))
        with self.assertRaises(ValueError):
            validate_uniform_session_hours((4, 8, 12, 16, 20))


if __name__ == "__main__":
    unittest.main()
