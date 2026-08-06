from __future__ import annotations

import unittest
from pathlib import Path

from nt_lvcfr_data import CandidateConfig, NS_PER_MINUTE
from nt_lvcfr_trade_proxy import (
    merge_signal_windows,
    parse_aggtrade_row,
    select_trade_second_extrema,
)


class AggTradeParsingTests(unittest.TestCase):
    def test_millisecond_timestamp_and_side(self) -> None:
        row = ["7", "40000.1", "0.125", "1", "2", "1700000000123", "true"]
        parsed = parse_aggtrade_row(row)
        self.assertEqual(parsed[0], 7)
        self.assertEqual(parsed[1], 40000.1)
        self.assertEqual(parsed[2], 0.125)
        self.assertTrue(parsed[3])
        self.assertEqual(parsed[4], 1_700_000_000_123_000_000)
        self.assertEqual(parsed[4], parsed[5])

    def test_microsecond_timestamp(self) -> None:
        row = ["8", "40001", "1", "1", "2", "1700000000123456", "false"]
        parsed = parse_aggtrade_row(row)
        self.assertFalse(parsed[3])
        self.assertEqual(parsed[4], 1_700_000_000_123_456_000)

    def test_short_row_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_aggtrade_row(["1", "2"])


class TradeEnvelopeTests(unittest.TestCase):
    def test_first_last_and_extrema_preserve_original_order(self) -> None:
        rows = [
            (1, 100.0, 1.0, False, 1, 1),
            (2, 102.0, 1.0, False, 2, 2),
            (3, 99.0, 1.0, True, 3, 3),
            (4, 101.0, 1.0, True, 4, 4),
            (5, 100.5, 1.0, False, 5, 5),
        ]
        selected = select_trade_second_extrema(rows)
        self.assertEqual([row[0] for row in selected], [1, 2, 3, 5])
        self.assertEqual(selected, sorted(selected, key=lambda row: row[5]))


class WindowTests(unittest.TestCase):
    def test_windows_cover_continuation_and_failure_reversal(self) -> None:
        config = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v3_config.json"))
        signals = [
            {"confirm_time_ns": 100 * NS_PER_MINUTE},
            {"confirm_time_ns": 200 * NS_PER_MINUTE},
        ]
        end_ns = 1_000 * NS_PER_MINUTE
        windows = merge_signal_windows(signals, config, end_ns)
        horizon = max(
            config.continuation_max_holding_minutes,
            config.rapid_failure_minutes
            + config.reversal_entry_delay_minutes
            + config.reversal_max_holding_minutes,
        ) + 5
        self.assertEqual(
            windows[0],
            (100 * NS_PER_MINUTE, (200 + horizon) * NS_PER_MINUTE),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
