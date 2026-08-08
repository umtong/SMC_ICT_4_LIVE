from __future__ import annotations

from datetime import datetime, timezone
import unittest

from quarter_hour_router import QuarterHourContext
from quarter_hour_router import QuarterHourThresholds
from quarter_hour_router import detect_opening_acceptance
from quarter_hour_router import evaluate_defended_retest
from quarter_hour_router import is_utc_quarter_hour


def ns(year: int, month: int, day: int, hour: int, minute: int) -> int:
    moment = datetime(
        year,
        month,
        day,
        hour,
        minute,
        59,
        999000,
        tzinfo=timezone.utc,
    )
    return int(moment.timestamp() * 1_000_000_000)


class QuarterHourRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.thresholds = QuarterHourThresholds()

    def test_clock_context_uses_utc_quarter_hours_only(self) -> None:
        self.assertTrue(is_utc_quarter_hour(ns(2024, 1, 2, 12, 15)))
        self.assertTrue(is_utc_quarter_hour(ns(2024, 1, 2, 12, 30)))
        self.assertFalse(is_utc_quarter_hour(ns(2024, 1, 2, 12, 14)))

    def test_opening_acceptance_separates_initiation_from_completed_state(self) -> None:
        context = detect_opening_acceptance(
            ts_event_ns=ns(2024, 1, 2, 12, 15),
            prior_highs=[100.0, 100.6, 100.4],
            prior_lows=[99.2, 99.5, 99.8],
            opening_high=101.5,
            opening_low=100.2,
            opening_close=101.3,
            atr=1.0,
            opening_flow_10s=0.40,
            opening_notional_burst_10s=1.8,
            full_flow_60s=0.25,
            return_60s_bps=42.0,
            efficiency_60s=0.62,
            thresholds=self.thresholds,
        )
        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.side, 1)
        self.assertEqual(context.boundary, 100.6)

    def test_opposing_opening_flow_cannot_confirm_acceptance(self) -> None:
        context = detect_opening_acceptance(
            ts_event_ns=ns(2024, 1, 2, 12, 15),
            prior_highs=[100.0, 100.6, 100.4],
            prior_lows=[99.2, 99.5, 99.8],
            opening_high=101.5,
            opening_low=100.2,
            opening_close=101.3,
            atr=1.0,
            opening_flow_10s=-0.40,
            opening_notional_burst_10s=1.8,
            full_flow_60s=0.25,
            return_60s_bps=42.0,
            efficiency_60s=0.62,
            thresholds=self.thresholds,
        )
        self.assertIsNone(context)

    def test_later_retest_requires_price_flow_and_queue_defense(self) -> None:
        context = QuarterHourContext(
            side=1,
            boundary=100.6,
            opposite_boundary=99.2,
            opening_extreme=101.5,
            atr=1.0,
            opening_time_ns=ns(2024, 1, 2, 12, 15),
        )
        decision = evaluate_defended_retest(
            context=context,
            high=101.2,
            low=100.55,
            close=101.05,
            tail_flow_15s=0.10,
            depth_imbalance_1=0.08,
            thresholds=self.thresholds,
        )
        self.assertEqual(decision.state, "CONFIRMED")

        opposed = evaluate_defended_retest(
            context=context,
            high=101.2,
            low=100.55,
            close=101.05,
            tail_flow_15s=-0.10,
            depth_imbalance_1=0.08,
            thresholds=self.thresholds,
        )
        self.assertEqual(opposed.state, "WAITING")
        self.assertEqual(opposed.reason, "RETEST_TAIL_FLOW_OPPOSED_ACCEPTANCE")

    def test_lost_range_boundary_invalidates_before_entry(self) -> None:
        context = QuarterHourContext(
            side=-1,
            boundary=99.2,
            opposite_boundary=100.6,
            opening_extreme=98.5,
            atr=1.0,
            opening_time_ns=ns(2024, 1, 2, 12, 15),
        )
        decision = evaluate_defended_retest(
            context=context,
            high=99.5,
            low=98.8,
            close=99.4,
            tail_flow_15s=-0.1,
            depth_imbalance_1=-0.1,
            thresholds=self.thresholds,
        )
        self.assertEqual(decision.state, "INVALIDATED")
        self.assertTrue(decision.invalidated)


if __name__ == "__main__":
    unittest.main()
