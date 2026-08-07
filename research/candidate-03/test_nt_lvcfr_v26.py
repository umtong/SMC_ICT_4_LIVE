from __future__ import annotations

import unittest

from derive_nt_lvcfr_v24_signals import OneBar
from derive_nt_lvcfr_v26_signals import (
    find_failed_gap_retest,
    find_reversal_failure,
)


def bar(
    minute: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    flow: float,
) -> OneBar:
    notional = 1_000_000.0
    return OneBar(
        start_minute=minute,
        open=open_,
        high=high,
        low=low,
        close=close,
        notional=notional,
        signed_notional=flow * notional,
    )


class FailedReversalTrapTests(unittest.TestCase):
    def test_bullish_reversal_failure_requires_both_markets_selling(self) -> None:
        futures = [
            bar(0, open_=101.0, high=101.2, low=100.4, close=100.6, flow=-0.40),
            bar(1, open_=100.6, high=100.7, low=99.7, close=99.9, flow=-0.45),
        ]
        disagreeing_spot = [
            bar(0, open_=101.0, high=101.2, low=100.4, close=100.6, flow=0.10),
            bar(1, open_=100.6, high=100.7, low=99.7, close=99.9, flow=0.20),
        ]
        failure, reason = find_reversal_failure(
            futures,
            disagreeing_spot,
            start_index=0,
            source_direction=1,
            gap_lower=100.0,
            gap_upper=101.0,
            expiry_minutes=2,
        )
        self.assertIsNone(failure)
        self.assertEqual(reason, "REVERSAL_FAILURE_UNRESOLVED")

        agreeing_spot = [
            bar(0, open_=101.0, high=101.2, low=100.4, close=100.6, flow=-0.20),
            bar(1, open_=100.6, high=100.7, low=99.7, close=99.9, flow=-0.30),
        ]
        failure, reason = find_reversal_failure(
            futures,
            agreeing_spot,
            start_index=0,
            source_direction=1,
            gap_lower=100.0,
            gap_upper=101.0,
            expiry_minutes=2,
        )
        self.assertIsNotNone(failure)
        self.assertEqual(failure.index, 1)
        self.assertEqual(reason, "FAILED_REVERSAL_CONFIRMED")

    def test_failed_bullish_gap_retest_confirms_bearish_continuation(self) -> None:
        futures = [
            bar(0, open_=99.6, high=100.4, low=99.4, close=99.7, flow=-0.10),
            bar(1, open_=100.2, high=100.7, low=99.5, close=99.8, flow=-0.35),
        ]
        spot = [
            bar(0, open_=99.6, high=100.4, low=99.4, close=99.7, flow=-0.05),
            bar(1, open_=100.2, high=100.7, low=99.5, close=99.8, flow=-0.20),
        ]
        index, confirmed, touches = find_failed_gap_retest(
            futures,
            spot,
            start_index=0,
            source_direction=1,
            gap_lower=100.0,
            gap_upper=101.0,
            expiry_minutes=2,
        )
        self.assertEqual(index, 1)
        self.assertIsNotNone(confirmed)
        self.assertEqual(touches, 2)

    def test_full_gap_reclaim_cancels_failed_gap_continuation(self) -> None:
        futures = [
            bar(0, open_=99.7, high=101.3, low=99.5, close=101.1, flow=0.30),
        ]
        spot = [
            bar(0, open_=99.7, high=101.3, low=99.5, close=101.1, flow=0.25),
        ]
        index, confirmed, reason = find_failed_gap_retest(
            futures,
            spot,
            start_index=0,
            source_direction=1,
            gap_lower=100.0,
            gap_upper=101.0,
            expiry_minutes=1,
        )
        self.assertIsNone(index)
        self.assertIsNone(confirmed)
        self.assertEqual(reason, "FAILED_BULLISH_GAP_FULLY_RECLAIMED")

    def test_short_reversal_failure_is_symmetric(self) -> None:
        futures = [
            bar(0, open_=100.0, high=101.3, low=99.8, close=101.2, flow=0.40),
        ]
        spot = [
            bar(0, open_=100.0, high=101.3, low=99.8, close=101.2, flow=0.20),
        ]
        failure, reason = find_reversal_failure(
            futures,
            spot,
            start_index=0,
            source_direction=-1,
            gap_lower=99.0,
            gap_upper=101.0,
            expiry_minutes=1,
        )
        self.assertIsNotNone(failure)
        self.assertEqual(reason, "FAILED_REVERSAL_CONFIRMED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
