from __future__ import annotations

import unittest

from c10_l1_data import RawQuote
from c10_l1_data import RawTrade
from c10_l1_data import align_latest_known_quotes
from c10_l1_replay import _record_market_events
from c10_l1_replay import chunk_events_by_timestamp
from c10_strategy import make_cost_loaded_btc_perpetual


class _Event:
    def __init__(self, ts_init: int, label: str):
        self.ts_init = ts_init
        self.label = label


class L1AlignmentTests(unittest.TestCase):
    def test_alignment_never_uses_future_quote(self) -> None:
        quotes = iter(
            [
                RawQuote(1, 100, "99.9", "1.000", "100.0", "2.000"),
                RawQuote(2, 200, "100.0", "3.000", "100.1", "4.000"),
            ],
        )
        trades = iter(
            [
                RawTrade(10, 90, "99.9", "0.010", 1),
                RawTrade(11, 100, "100.0", "0.020", 1),
                RawTrade(12, 199, "100.0", "0.030", -1),
                RawTrade(13, 200, "100.1", "0.040", 1),
            ],
        )
        aligned = list(align_latest_known_quotes(quotes, trades))
        self.assertIsNone(aligned[0].quote)
        self.assertEqual(aligned[1].quote.update_id, 1)
        self.assertEqual(aligned[2].quote.update_id, 1)
        self.assertEqual(aligned[3].quote.update_id, 2)
        for item in aligned:
            if item.quote is not None:
                self.assertLessEqual(item.quote.ts_ns, item.trade.ts_ns)

    def test_record_replays_quote_before_equal_timestamp_trade(self) -> None:
        instrument = make_cost_loaded_btc_perpetual()
        state = {"last_quote_id": None}
        record = (
            10,
            200,
            100.0,
            0.010,
            1,
            2,
            200,
            99.9,
            1.0,
            100.0,
            2.0,
        )
        events = _record_market_events(
            record,
            instrument=instrument,
            replay_state=state,
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].__class__.__name__, "QuoteTick")
        self.assertEqual(events[1].__class__.__name__, "TradeTick")
        self.assertEqual(events[0].ts_init, events[1].ts_init)
        self.assertLessEqual(events[0].ts_event, events[1].ts_event)

    def test_unchanged_quote_is_not_duplicated(self) -> None:
        instrument = make_cost_loaded_btc_perpetual()
        state = {"last_quote_id": None}
        first = (
            10, 200, 100.0, 0.010, 1,
            2, 190, 99.9, 1.0, 100.0, 2.0,
        )
        second = (
            11, 201, 100.0, 0.020, -1,
            2, 190, 99.9, 1.0, 100.0, 2.0,
        )
        self.assertEqual(
            len(_record_market_events(first, instrument=instrument, replay_state=state)),
            2,
        )
        second_events = _record_market_events(
            second,
            instrument=instrument,
            replay_state=state,
        )
        self.assertEqual(len(second_events), 1)
        self.assertEqual(second_events[0].__class__.__name__, "TradeTick")

    def test_chunking_does_not_split_equal_timestamp_group(self) -> None:
        events = [
            _Event(1, "a"),
            _Event(1, "b"),
            _Event(2, "c"),
            _Event(2, "d"),
            _Event(3, "e"),
        ]
        chunks = list(chunk_events_by_timestamp(events, maximum_events=2))
        self.assertEqual([[item.label for item in chunk] for chunk in chunks],
                         [["a", "b"], ["c", "d"], ["e"]])


if __name__ == "__main__":
    unittest.main()
