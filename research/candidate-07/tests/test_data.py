from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from data import _kline_archive_requests  # noqa: E402


class KlineArchivePlanningTests(unittest.TestCase):
    def test_complete_calendar_month_uses_one_monthly_archive(self) -> None:
        requests = _kline_archive_requests(
            date(2025, 1, 1),
            date(2025, 2, 1),
        )
        self.assertEqual(
            [(item.cadence, item.stamp) for item in requests],
            [("monthly", "2025-01")],
        )

    def test_partial_week_uses_exact_daily_archives(self) -> None:
        requests = _kline_archive_requests(
            date(2025, 1, 27),
            date(2025, 2, 3),
        )
        self.assertEqual(len(requests), 7)
        self.assertTrue(all(item.cadence == "daily" for item in requests))
        self.assertEqual(requests[0].stamp, "2025-01-27")
        self.assertEqual(requests[-1].stamp, "2025-02-02")

    def test_long_interval_uses_daily_warmup_then_complete_months(self) -> None:
        requests = _kline_archive_requests(
            date(2023, 12, 29),
            date(2026, 7, 1),
        )
        daily = [item for item in requests if item.cadence == "daily"]
        monthly = [item for item in requests if item.cadence == "monthly"]
        self.assertEqual([item.stamp for item in daily], [
            "2023-12-29",
            "2023-12-30",
            "2023-12-31",
        ])
        self.assertEqual(len(monthly), 30)
        self.assertEqual(monthly[0].stamp, "2024-01")
        self.assertEqual(monthly[-1].stamp, "2026-06")

    def test_invalid_interval_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _kline_archive_requests(
                date(2025, 1, 1),
                date(2025, 1, 1),
            )


if __name__ == "__main__":
    unittest.main()
