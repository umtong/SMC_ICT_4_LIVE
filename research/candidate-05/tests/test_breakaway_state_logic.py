from __future__ import annotations

import unittest

from breakaway_state_logic import no_retrace_breakaway_allowed


class BreakawayStateLogicTests(unittest.TestCase):
    def test_breakaway_is_allowed_only_before_any_retest_touch(self) -> None:
        self.assertTrue(
            no_retrace_breakaway_allowed(
                retest_touch_count=0,
                breakaway_candidate=True,
            ),
        )
        self.assertFalse(
            no_retrace_breakaway_allowed(
                retest_touch_count=1,
                breakaway_candidate=True,
            ),
        )
        self.assertFalse(
            no_retrace_breakaway_allowed(
                retest_touch_count=2,
                breakaway_candidate=True,
            ),
        )
        self.assertFalse(
            no_retrace_breakaway_allowed(
                retest_touch_count=0,
                breakaway_candidate=False,
            ),
        )

    def test_negative_touch_count_is_invalid_state(self) -> None:
        with self.assertRaises(ValueError):
            no_retrace_breakaway_allowed(
                retest_touch_count=-1,
                breakaway_candidate=True,
            )


if __name__ == "__main__":
    unittest.main()
