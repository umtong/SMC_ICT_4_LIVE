from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from nt_lvcfr_data import NS_PER_MINUTE
from prepare_nt_lvcfr_v18_sparse import (
    required_book_ticker_dates,
    utc_dates_touched,
)


def ns(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1e9)


class SparseV18PreparationTests(unittest.TestCase):
    def test_window_touching_midnight_requires_both_utc_dates(self) -> None:
        touched = utc_dates_touched(
            ns("2024-01-08T23:59:30"),
            ns("2024-01-09T00:00:30"),
        )
        self.assertEqual(touched, {date(2024, 1, 8), date(2024, 1, 9)})

    def test_candidate_dates_cover_pre_event_baseline_and_observation(self) -> None:
        signal = {
            "first_start_time_ns": ns("2024-01-09T00:05:00"),
            "confirm_time_ns": ns("2024-01-09T00:15:00"),
        }
        self.assertEqual(
            required_book_ticker_dates([signal]),
            [date(2024, 1, 8), date(2024, 1, 9)],
        )

    def test_detector_schedule_is_not_modified(self) -> None:
        signal = {
            "scenario_id": "unchanged",
            "first_start_time_ns": 100 * NS_PER_MINUTE,
            "confirm_time_ns": 110 * NS_PER_MINUTE,
        }
        snapshot = dict(signal)
        required_book_ticker_dates([signal])
        self.assertEqual(signal, snapshot)


if __name__ == "__main__":
    unittest.main(verbosity=2)
