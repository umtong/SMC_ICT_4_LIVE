from __future__ import annotations

import unittest

import pandas as pd

from latency_aligned_tick_clock import EXECUTION_OFFSET_NS
from latency_aligned_tick_clock import MODELED_ORDER_LATENCY_NS
from latency_aligned_tick_clock import SAFETY_MARGIN_NS
from latency_aligned_tick_clock import _select_latency_aligned_rows


class LatencyAlignedTickClockTests(unittest.TestCase):
    @staticmethod
    def _rows() -> pd.DataFrame:
        minute = 60_000_000_000
        return pd.DataFrame(
            {
                "ts_ns": [
                    100_000_000,
                    260_000_000,
                    320_000_000,
                    500_000_000,
                    minute + 90_000_000,
                    minute + 200_000_000,
                ],
                "minute_ns": [0, 0, 0, 0, minute, minute],
                "price": [100.0, 100.1, 100.2, 100.3, 101.0, 101.1],
                "quantity": [1.0] * 6,
                "trade_id": [str(index) for index in range(6)],
                "buyer_maker": [False, False, True, False, True, False],
            },
        )

    def test_offset_matches_modeled_latency_plus_safety(self) -> None:
        self.assertEqual(
            EXECUTION_OFFSET_NS,
            MODELED_ORDER_LATENCY_NS + SAFETY_MARGIN_NS,
        )
        self.assertGreater(SAFETY_MARGIN_NS, 0)

    def test_earliest_latency_eligible_trade_is_selected(self) -> None:
        selected = _select_latency_aligned_rows(self._rows())
        first = selected.iloc[0]
        self.assertEqual(int(first["ts_ns"]), 320_000_000)
        self.assertTrue(bool(first["latency_ready"]))
        self.assertGreaterEqual(int(first["offset_ns"]), EXECUTION_OFFSET_NS)

    def test_first_real_trade_is_fail_safe_fallback(self) -> None:
        selected = _select_latency_aligned_rows(self._rows())
        second = selected.iloc[1]
        self.assertEqual(int(second["ts_ns"]), 60_090_000_000)
        self.assertFalse(bool(second["latency_ready"]))

    def test_selection_is_one_per_minute_and_ordered(self) -> None:
        selected = _select_latency_aligned_rows(self._rows())
        self.assertEqual(len(selected), 2)
        self.assertFalse(selected["minute_ns"].duplicated().any())
        self.assertTrue(selected["ts_ns"].is_monotonic_increasing)


if __name__ == "__main__":
    unittest.main()
