from __future__ import annotations

from datetime import date
import unittest

import select_untouched_weeks as selector


class UntouchedWeekSelectionTests(unittest.TestCase):
    def test_eligible_calendar_excludes_every_development_build_window(self) -> None:
        weeks = selector.eligible_weeks()
        self.assertGreater(len(weeks), 100)
        for week in weeks:
            for start, end in selector.DEFAULT_EXCLUDED_BUILD_WINDOWS:
                self.assertFalse(
                    selector.windows_overlap(
                        week.build_start,
                        week.build_end,
                        start,
                        end,
                    )
                )

    def test_selection_is_deterministic_and_non_overlapping(self) -> None:
        commit = "0123456789abcdef0123456789abcdef01234567"
        first = selector.select_sequential_weeks(commit)
        second = selector.select_sequential_weeks(commit)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)
        weeks = [item[1] for item in first]
        for index, week in enumerate(weeks):
            for other in weeks[index + 1 :]:
                self.assertFalse(
                    selector.windows_overlap(
                        week.build_start,
                        week.build_end,
                        other.build_start,
                        other.build_end,
                    )
                )

    def test_record_contains_selection_before_market_data_contract(self) -> None:
        commit = "a" * 40
        record = selector.selection_record(commit)
        contract = record["selection_contract"]
        self.assertFalse(contract["market_data_read_before_selection"])
        self.assertFalse(contract["result_data_read_before_selection"])
        self.assertEqual(len(record["selected"]), 3)
        for row in record["selected"]:
            self.assertEqual(
                date.fromisoformat(row["evaluation_start"]).weekday(),
                0,
            )

    def test_invalid_commit_or_count_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            selector.select_sequential_weeks("abc")
        with self.assertRaises(ValueError):
            selector.select_sequential_weeks("b" * 40, count=0)

    def test_calendar_bounds_must_be_mondays(self) -> None:
        with self.assertRaises(ValueError):
            selector.eligible_weeks(
                calendar_start=date(2023, 1, 3),
                calendar_end=date(2023, 1, 9),
            )


if __name__ == "__main__":
    unittest.main()
