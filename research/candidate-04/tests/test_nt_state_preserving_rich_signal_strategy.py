from __future__ import annotations

import unittest

from nt_state_preserving_rich_signal_strategy import entry_fill_preserves_state


class EntryFillStateTests(unittest.TestCase):
    def test_long_fill_must_remain_above_completed_structure(self) -> None:
        self.assertTrue(entry_fill_preserves_state(101.0, 100.0, 1))
        self.assertFalse(entry_fill_preserves_state(100.0, 100.0, 1))
        self.assertFalse(entry_fill_preserves_state(99.9, 100.0, 1))

    def test_short_fill_must_remain_below_completed_structure(self) -> None:
        self.assertTrue(entry_fill_preserves_state(99.0, 100.0, -1))
        self.assertFalse(entry_fill_preserves_state(100.0, 100.0, -1))
        self.assertFalse(entry_fill_preserves_state(100.1, 100.0, -1))

    def test_invalid_side_fails_closed(self) -> None:
        self.assertFalse(entry_fill_preserves_state(101.0, 100.0, 0))


if __name__ == "__main__":
    unittest.main()
