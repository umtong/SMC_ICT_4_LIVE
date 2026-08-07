"""Execution-price and forced-exit causality contracts for quote resiliency."""

from __future__ import annotations

import unittest

import pandas as pd

from quote_resiliency_signals import executable_quote_reference
from quote_resiliency_strategy import (
    expected_one_tick_entry_fill,
    fill_adjusted_exit_is_causal,
)


class ExecutableQuoteReferenceContracts(unittest.TestCase):
    def test_long_uses_completed_best_ask_not_trade_close(self) -> None:
        row = pd.Series(
            {
                "close": 100.0,
                "bid_close": 99.9,
                "ask_close": 100.1,
            }
        )
        self.assertEqual(executable_quote_reference(row, 1), 100.1)

    def test_short_uses_completed_best_bid_not_trade_close(self) -> None:
        row = pd.Series(
            {
                "close": 100.0,
                "bid_close": 99.9,
                "ask_close": 100.1,
            }
        )
        self.assertEqual(executable_quote_reference(row, -1), 99.9)

    def test_invalid_or_crossed_completed_quote_fails_closed(self) -> None:
        for row in (
            pd.Series({"bid_close": 100.2, "ask_close": 100.1}),
            pd.Series({"bid_close": 0.0, "ask_close": 100.1}),
            pd.Series({"bid_close": 100.0, "ask_close": float("nan")}),
        ):
            with self.assertRaises(ValueError):
                executable_quote_reference(row, 1)

    def test_direction_must_be_signed(self) -> None:
        row = pd.Series({"bid_close": 99.9, "ask_close": 100.1})
        with self.assertRaises(ValueError):
            executable_quote_reference(row, 0)


class ExpectedOneTickFillContracts(unittest.TestCase):
    def test_long_and_short_fill_one_tick_adverse_from_l1(self) -> None:
        self.assertAlmostEqual(
            expected_one_tick_entry_fill(100.1, 1, 0.1),
            100.2,
            places=12,
        )
        self.assertAlmostEqual(
            expected_one_tick_entry_fill(99.9, -1, 0.1),
            99.8,
            places=12,
        )

    def test_invalid_direction_or_nonpositive_inputs_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            expected_one_tick_entry_fill(100.0, 0, 0.1)
        with self.assertRaises(ValueError):
            expected_one_tick_entry_fill(0.0, 1, 0.1)
        with self.assertRaises(ValueError):
            expected_one_tick_entry_fill(100.0, 1, 0.0)


class ExpectedOneTickFillContracts(unittest.TestCase):
    def test_long_and_short_fill_one_tick_adverse_from_l1(self) -> None:
        self.assertAlmostEqual(expected_one_tick_entry_fill(100.1, 1, 0.1), 100.2, places=12)
        self.assertAlmostEqual(expected_one_tick_entry_fill(99.9, -1, 0.1), 99.8, places=12)

    def test_invalid_direction_or_nonpositive_inputs_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            expected_one_tick_entry_fill(100.0, 0, 0.1)
        with self.assertRaises(ValueError):
            expected_one_tick_entry_fill(0.0, 1, 0.1)
        with self.assertRaises(ValueError):
            expected_one_tick_entry_fill(100.0, 1, 0.0)


class ExpectedOneTickFillContracts(unittest.TestCase):
    def test_long_and_short_fill_one_tick_adverse_from_l1(self) -> None:
        self.assertAlmostEqual(expected_one_tick_entry_fill(100.1, 1, 0.1), 100.2, places=12)
        self.assertAlmostEqual(expected_one_tick_entry_fill(99.9, -1, 0.1), 99.8, places=12)

    def test_invalid_direction_or_nonpositive_inputs_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            expected_one_tick_entry_fill(100.0, 0, 0.1)
        with self.assertRaises(ValueError):
            expected_one_tick_entry_fill(0.0, 1, 0.1)
        with self.assertRaises(ValueError):
            expected_one_tick_entry_fill(100.0, 1, 0.0)


class FillAdjustedExitTimingContracts(unittest.TestCase):
    def test_fill_adjusted_exit_requires_strictly_later_event_time(self) -> None:
        reason = "FILL_ADJUSTED_RISK_BUDGET_EXCEEDED"
        opened = 1_000
        self.assertFalse(fill_adjusted_exit_is_causal(reason, 999, opened))
        self.assertFalse(fill_adjusted_exit_is_causal(reason, 1_000, opened))
        self.assertTrue(fill_adjusted_exit_is_causal(reason, 1_001, opened))

    def test_non_risk_exit_is_not_changed_by_guard(self) -> None:
        self.assertTrue(fill_adjusted_exit_is_causal("EVENT_TIME_TIMEOUT", 1_000, 1_000))

    def test_fill_adjusted_exit_without_position_open_time_fails_closed(self) -> None:
        self.assertFalse(
            fill_adjusted_exit_is_causal(
                "FILL_ADJUSTED_RISK_BUDGET_EXCEEDED",
                1_000,
                None,
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
