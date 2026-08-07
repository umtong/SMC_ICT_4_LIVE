from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from semantic_execution import (
    MARKET_ENTRY_SENTINEL_NS,
    install,
    is_market_entry_expiry,
)


class SemanticExecutionBoundaryTests(unittest.TestCase):
    def test_market_sentinel_matches_runner_microsecond_rounding(self):
        value = datetime.fromtimestamp(
            MARKET_ENTRY_SENTINEL_NS / 1_000_000_000,
            tz=timezone.utc,
        ) + timedelta(microseconds=1)
        self.assertTrue(is_market_entry_expiry(value))
        self.assertFalse(is_market_entry_expiry(datetime(2025, 1, 1, tzinfo=timezone.utc)))

    def test_order_factory_boundary_is_patchable_and_idempotent(self):
        install()
        install()


if __name__ == "__main__":
    unittest.main()
