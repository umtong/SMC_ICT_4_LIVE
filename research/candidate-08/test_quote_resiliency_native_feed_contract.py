"""Causal ordering contracts for native quote-resiliency execution data."""

from __future__ import annotations

from pathlib import Path
import unittest

import pandas as pd

from quote_resiliency_features_v3 import aggregate_quote_events, quote_event_rows


class NativeQuoteFeedOrderingContracts(unittest.TestCase):
    @staticmethod
    def _events():
        index = pd.DatetimeIndex(
            [
                "2023-10-15T00:00:01.000000Z",
                "2023-10-15T00:00:01.000000Z",
                "2023-10-15T00:00:09.999000Z",
                "2023-10-15T00:00:10.001000Z",
                "2023-10-15T00:00:19.500000Z",
            ]
        )
        quotes = pd.DataFrame(
            {
                "best_bid_price": [100.0, 100.0, 99.9, 99.9, 100.0],
                "best_bid_qty": [10.0, 12.0, 9.0, 8.0, 7.0],
                "best_ask_price": [100.1, 100.1, 100.1, 100.2, 100.1],
                "best_ask_qty": [8.0, 7.0, 10.0, 9.0, 6.0],
            },
            index=index,
        )
        return quote_event_rows(
            quotes,
            previous_quote={
                "best_bid_price": 100.0,
                "best_bid_qty": 9.0,
                "best_ask_price": 100.1,
                "best_ask_qty": 9.0,
            },
        )[0]

    def test_each_bucket_retains_exact_first_and_last_source_event_time(self) -> None:
        events = self._events()
        buckets = aggregate_quote_events(events, cadence_seconds=10)
        first = buckets.loc[pd.Timestamp("2023-10-15T00:00:10Z")]
        second = buckets.loc[pd.Timestamp("2023-10-15T00:00:20Z")]
        self.assertEqual(
            first["quote_first_event_time"],
            pd.Timestamp("2023-10-15T00:00:01Z"),
        )
        self.assertEqual(
            first["quote_last_event_time"],
            pd.Timestamp("2023-10-15T00:00:09.999Z"),
        )
        self.assertEqual(
            second["quote_first_event_time"],
            pd.Timestamp("2023-10-15T00:00:10.001Z"),
        )
        self.assertEqual(
            second["quote_last_event_time"],
            pd.Timestamp("2023-10-15T00:00:19.500Z"),
        )

    def test_source_event_time_is_chunk_invariant(self) -> None:
        events = self._events()
        whole = aggregate_quote_events(events, cadence_seconds=10)
        first = aggregate_quote_events(events.iloc[:3], cadence_seconds=10)
        second = aggregate_quote_events(events.iloc[3:], cadence_seconds=10)
        split = pd.concat([first, second]).sort_index()
        pd.testing.assert_frame_equal(whole, split)

    def test_native_runner_declares_quote_after_bar_execution_contract(self) -> None:
        source = Path(__file__).with_name("run_quote_resiliency_nautilus.py").read_text(
            encoding="utf-8"
        )
        strategy = Path(__file__).with_name("quote_resiliency_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("completion_quote_ticks_from_frame", source)
        self.assertIn("NATIVE_QUOTE_REVISION", source)
        self.assertIn("base_runner._quote_ticks_from_ten_second_frame", source)
        self.assertIn("def on_quote_tick", strategy)
        self.assertIn("COMPLETION_DELAY_NS", strategy)
        self.assertIn("_process_signal_time(", strategy)
        self.assertIn("observed_time_ns=quote_time_ns", strategy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
