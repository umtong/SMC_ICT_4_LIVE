from __future__ import annotations

import unittest

from candidate15_portfolio_materializer import far_stop_preserves_sweep_invalidation


class FarInvalidationInvariantTests(unittest.TestCase):
    def test_long_requires_stop_at_or_below_sweep_invalidation(self) -> None:
        self.assertTrue(far_stop_preserves_sweep_invalidation("LONG", 99.0, 99.0))
        self.assertTrue(far_stop_preserves_sweep_invalidation("LONG", 98.9, 99.0))
        self.assertFalse(far_stop_preserves_sweep_invalidation("LONG", 99.1, 99.0))

    def test_short_requires_stop_at_or_above_sweep_invalidation(self) -> None:
        self.assertTrue(far_stop_preserves_sweep_invalidation("SHORT", 101.0, 101.0))
        self.assertTrue(far_stop_preserves_sweep_invalidation("SHORT", 101.1, 101.0))
        self.assertFalse(far_stop_preserves_sweep_invalidation("SHORT", 100.9, 101.0))

    def test_missing_or_invalid_reference_fails_closed(self) -> None:
        self.assertFalse(far_stop_preserves_sweep_invalidation("LONG", 99.0, None))
        self.assertFalse(far_stop_preserves_sweep_invalidation("UNKNOWN", 99.0, 99.0))


if __name__ == "__main__":
    unittest.main()
