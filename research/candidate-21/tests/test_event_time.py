from __future__ import annotations

import math
import unittest

import pandas as pd

from event_time_data import BUCKET_NS
from event_time_data import EXECUTION_OFFSET_NS
from event_time_data import _aggregate_trade_rows
from event_time_data import _merge_bar_pieces
from event_time_data import _select_bucket_ticks
from event_time_data import build_feature_frame
from event_time_router import EventDecision
from event_time_router import TenSecondEvent
from event_time_router import TenSecondResponse
from event_time_router import classify_response


def _event(direction: int = 1) -> TenSecondEvent:
    if direction > 0:
        return TenSecondEvent(
            scenario_id="event-long",
            direction=1,
            event_index=10,
            boundary_level=100.0,
            range_opposite=90.0,
            acceptance_target=110.0,
            rejection_target=90.0,
            atr=2.0,
            event_open=99.0,
            event_high=102.0,
            event_low=98.0,
            event_close=101.0,
            event_flow=0.35,
            event_notional=1_000_000.0,
            phase_burst=1.5,
        )
    return TenSecondEvent(
        scenario_id="event-short",
        direction=-1,
        event_index=10,
        boundary_level=100.0,
        range_opposite=110.0,
        acceptance_target=90.0,
        rejection_target=110.0,
        atr=2.0,
        event_open=101.0,
        event_high=102.0,
        event_low=98.0,
        event_close=99.0,
        event_flow=-0.35,
        event_notional=1_000_000.0,
        phase_burst=1.5,
    )


class EventTimeRouterTests(unittest.TestCase):
    def test_acceptance_requires_immediate_non_overlapping_transmission(self) -> None:
        decision, reason, details = classify_response(
            _event(1),
            TenSecondResponse(
                bar_index=11,
                open=101.0,
                high=104.0,
                low=100.5,
                close=103.0,
                flow=0.20,
                return_bps=19.0,
                efficiency=0.65,
            ),
        )
        self.assertIs(decision, EventDecision.ACCEPTANCE)
        self.assertIn("TRANSMITTED", reason)
        self.assertGreater(details["directional_outside"], 0.0)

    def test_failed_auction_requires_reentry_and_opposite_flow(self) -> None:
        decision, reason, _ = classify_response(
            _event(1),
            TenSecondResponse(
                bar_index=11,
                open=101.0,
                high=101.5,
                low=96.0,
                close=97.0,
                flow=-0.25,
                return_bps=-39.0,
                efficiency=0.72,
            ),
        )
        self.assertIs(decision, EventDecision.FAILED_AUCTION)
        self.assertIn("REENTERED", reason)

    def test_mixed_response_is_no_trade(self) -> None:
        decision, _, _ = classify_response(
            _event(1),
            TenSecondResponse(
                bar_index=11,
                open=101.0,
                high=103.0,
                low=99.0,
                close=101.5,
                flow=-0.05,
                return_bps=4.0,
                efficiency=0.10,
            ),
        )
        self.assertIs(decision, EventDecision.UNRESOLVED)

    def test_natural_target_consumed_before_entry_invalidates(self) -> None:
        decision, reason, _ = classify_response(
            _event(1),
            TenSecondResponse(
                bar_index=11,
                open=101.0,
                high=111.0,
                low=100.0,
                close=109.0,
                flow=0.40,
                return_bps=75.0,
                efficiency=0.80,
            ),
        )
        self.assertIs(decision, EventDecision.INVALIDATED)
        self.assertIn("TARGET_CONSUMED", reason)

    def test_only_immediate_next_bar_can_classify(self) -> None:
        with self.assertRaises(ValueError):
            classify_response(
                _event(1),
                TenSecondResponse(
                    bar_index=12,
                    open=101.0,
                    high=104.0,
                    low=100.5,
                    close=103.0,
                    flow=0.20,
                    return_bps=19.0,
                    efficiency=0.65,
                ),
            )

    def test_short_side_is_symmetric(self) -> None:
        decision, _, details = classify_response(
            _event(-1),
            TenSecondResponse(
                bar_index=11,
                open=99.0,
                high=99.5,
                low=96.0,
                close=97.0,
                flow=-0.20,
                return_bps=-20.0,
                efficiency=0.65,
            ),
        )
        self.assertIs(decision, EventDecision.ACCEPTANCE)
        self.assertGreater(details["directional_outside"], 0.0)


class EventTimeDataTests(unittest.TestCase):
    @staticmethod
    def _trade_rows() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_ns": [
                    100_000_000,
                    250_000_000,
                    320_000_000,
                    500_000_000,
                    BUCKET_NS + 50_000_000,
                    BUCKET_NS + 200_000_000,
                ],
                "bucket_ns": [
                    0,
                    0,
                    0,
                    0,
                    BUCKET_NS,
                    BUCKET_NS,
                ],
                "price": [100.0, 100.2, 100.3, 100.4, 101.0, 101.1],
                "quantity": [1.0] * 6,
                "notional": [100.0, 100.2, 100.3, 100.4, 101.0, 101.1],
                "signed_notional": [100.0, 100.2, -100.3, 100.4, -101.0, 101.1],
                "trade_id": [str(index) for index in range(6)],
                "buyer_maker": [False, False, True, False, True, False],
            },
        )

    def test_tick_selection_uses_first_real_trade_after_latency(self) -> None:
        selected = _select_bucket_ticks(self._trade_rows())
        first = selected.iloc[0]
        self.assertEqual(int(first["ts_ns"]), 320_000_000)
        self.assertTrue(bool(first["latency_ready"]))
        self.assertGreaterEqual(int(first["offset_ns"]), EXECUTION_OFFSET_NS)
        second = selected.iloc[1]
        self.assertEqual(int(second["ts_ns"]), BUCKET_NS + 50_000_000)
        self.assertFalse(bool(second["latency_ready"]))

    def test_split_chunks_merge_open_and_close_causally(self) -> None:
        rows = self._trade_rows().iloc[:4].copy()
        left = _aggregate_trade_rows(rows.iloc[:2])
        right = _aggregate_trade_rows(rows.iloc[2:])
        merged = _merge_bar_pieces([left, right])
        self.assertEqual(len(merged), 1)
        bar = merged.iloc[0]
        self.assertEqual(float(bar["open"]), 100.0)
        self.assertEqual(float(bar["close"]), 100.4)
        self.assertEqual(float(bar["high"]), 100.4)
        self.assertEqual(float(bar["low"]), 100.0)
        self.assertEqual(int(bar["trade_count"]), 4)

    def test_phase_baseline_excludes_current_event(self) -> None:
        starts = [
            index * 15 * 60 * 1_000_000_000
            for index in range(6)
        ]
        notionals = [100.0, 110.0, 90.0, 100.0, 100.0, 10_000.0]
        bars = pd.DataFrame(
            {
                "bucket_ns": starts,
                "first_ts": starts,
                "last_ts": [
                    value + BUCKET_NS - 1 for value in starts
                ],
                "open": [100.0] * 6,
                "high": [101.0] * 6,
                "low": [99.0] * 6,
                "close": [100.5] * 6,
                "volume": [1.0] * 6,
                "notional": notionals,
                "signed_notional": [50.0] * 6,
                "trade_count": [10] * 6,
            },
        )
        features = build_feature_frame(
            bars,
            period_minutes=15,
            baseline_periods=4,
            min_baseline_samples=2,
        )
        last = features.iloc[-1]
        self.assertAlmostEqual(
            float(last["event_notional_baseline"]),
            100.0,
        )
        self.assertAlmostEqual(
            float(last["event_phase_burst"]),
            100.0,
        )
        self.assertTrue(bool(last["event_feature_ready"]))

    def test_feature_observation_is_bar_completion(self) -> None:
        bars = pd.DataFrame(
            {
                "bucket_ns": [0],
                "first_ts": [100],
                "last_ts": [BUCKET_NS - 2],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1.0],
                "notional": [100.0],
                "signed_notional": [50.0],
                "trade_count": [10],
            },
        )
        features = build_feature_frame(
            bars,
            period_minutes=15,
            baseline_periods=4,
            min_baseline_samples=1,
        )
        self.assertEqual(
            int(features.iloc[0]["observed_time_ns"]),
            BUCKET_NS - 1,
        )
        self.assertTrue(math.isfinite(float(features.iloc[0]["flow_10s"])))


if __name__ == "__main__":
    unittest.main()
