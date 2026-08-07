from __future__ import annotations

import json
import unittest

import pandas as pd

from nautilus_trader.model.identifiers import InstrumentId
from parent_external_15s_mss_5s_retest_scenario import (
    build_signals,
    five_second_boundary_retest_index,
)
import run_local_liquidity_sweep_mss_retest as local


class FiveSecondSameBoundaryRetestTests(unittest.TestCase):
    @staticmethod
    def _bars(rows: list[dict[str, float | int]]) -> pd.DataFrame:
        frame = pd.DataFrame(rows)
        frame["timestamp_ns"] = frame["timestamp_ns"].map(int).astype(object)
        return frame

    def test_pre_mss_touch_cannot_become_entry(self) -> None:
        logic = local.LocalSweepMSSLogic()
        bars = self._bars(
            [
                {
                    "timestamp_ns": 14_999_999_999,
                    "open": 100.2,
                    "high": 100.4,
                    "low": 99.8,
                    "close": 100.3,
                    "range": 0.6,
                    "signed_quote": 10.0,
                },
                {
                    "timestamp_ns": 19_999_999_999,
                    "open": 100.1,
                    "high": 100.5,
                    "low": 99.9,
                    "close": 100.35,
                    "range": 0.6,
                    "signed_quote": 12.0,
                },
            ]
        )
        index, reason = five_second_boundary_retest_index(
            bars,
            mss_completed_ns=14_999_999_999,
            direction="LONG",
            boundary_level=100.0,
            event_extreme=98.0,
            event_atr=1.0,
            logic=logic,
        )
        self.assertEqual(index, 1)
        self.assertEqual(reason, "FIVE_SECOND_SAME_BOUNDARY_RETEST_CONFIRMED")

    def test_retest_must_reject_same_broken_boundary(self) -> None:
        logic = local.LocalSweepMSSLogic()
        bars = self._bars(
            [
                {
                    "timestamp_ns": 19_999_999_999,
                    "open": 99.7,
                    "high": 100.1,
                    "low": 99.5,
                    "close": 99.6,
                    "range": 0.6,
                    "signed_quote": -20.0,
                },
                {
                    "timestamp_ns": 24_999_999_999,
                    "open": 100.3,
                    "high": 100.4,
                    "low": 100.2,
                    "close": 100.35,
                    "range": 0.2,
                    "signed_quote": 15.0,
                },
            ]
        )
        index, reason = five_second_boundary_retest_index(
            bars,
            mss_completed_ns=14_999_999_999,
            direction="LONG",
            boundary_level=100.0,
            event_extreme=98.0,
            event_atr=1.0,
            logic=logic,
        )
        self.assertIsNone(index)
        self.assertEqual(reason, "FIVE_SECOND_SAME_BOUNDARY_RETEST_NOT_CONFIRMED")

    def test_source_invalidation_precedes_later_retest(self) -> None:
        logic = local.LocalSweepMSSLogic()
        bars = self._bars(
            [
                {
                    "timestamp_ns": 19_999_999_999,
                    "open": 99.0,
                    "high": 99.5,
                    "low": 97.9,
                    "close": 99.4,
                    "range": 1.6,
                    "signed_quote": 10.0,
                },
                {
                    "timestamp_ns": 24_999_999_999,
                    "open": 100.1,
                    "high": 100.3,
                    "low": 99.8,
                    "close": 100.2,
                    "range": 0.5,
                    "signed_quote": 10.0,
                },
            ]
        )
        index, reason = five_second_boundary_retest_index(
            bars,
            mss_completed_ns=14_999_999_999,
            direction="LONG",
            boundary_level=100.0,
            event_extreme=98.0,
            event_atr=1.0,
            logic=logic,
        )
        self.assertIsNone(index)
        self.assertEqual(reason, "SOURCE_INVALIDATED_DURING_5S_RETEST")

    def test_short_retest_is_symmetric(self) -> None:
        logic = local.LocalSweepMSSLogic()
        bars = self._bars(
            [
                {
                    "timestamp_ns": 19_999_999_999,
                    "open": 99.8,
                    "high": 100.2,
                    "low": 99.5,
                    "close": 99.6,
                    "range": 0.7,
                    "signed_quote": -12.0,
                }
            ]
        )
        index, reason = five_second_boundary_retest_index(
            bars,
            mss_completed_ns=14_999_999_999,
            direction="SHORT",
            boundary_level=100.0,
            event_extreme=102.0,
            event_atr=1.0,
            logic=logic,
        )
        self.assertEqual(index, 0)
        self.assertEqual(reason, "FIVE_SECOND_SAME_BOUNDARY_RETEST_CONFIRMED")


class HybridSignalContractTests(unittest.TestCase):
    def test_signal_contains_state_clock_ownership_without_future_path(self) -> None:
        observed_ns = 24_999_999_999
        report = {
            "summary": {},
            "scenarios": [
                {
                    "scenario_id": "hybrid-one",
                    "outcome": "ENTRY_READY",
                    "direction": "LONG",
                    "entry": 100.2,
                    "stop": 98.0,
                    "target": 104.6,
                    "expected_rr": 2.0,
                    "source_pool_id": "5ML-parent",
                    "source_timeframe": "5M",
                    "observed_time_ns": observed_ns,
                    "sweep": {
                        "timestamp_ns": 9_999_999_999,
                        "pool_id": "5ML-parent",
                        "source_timeframe": "5M",
                    },
                    "mss": {
                        "execution_timeframe": "15S",
                        "timestamp_ns": 14_999_999_999,
                        "boundary_pool_id": "15SH-boundary",
                        "boundary_level": 100.0,
                        "source_and_boundary_pivots_distinct": True,
                    },
                    "retest": {
                        "execution_timeframe": "5S",
                        "timestamp_ns": observed_ns,
                        "boundary_pool_id": "15SH-boundary",
                        "boundary_level": 100.0,
                        "same_boundary_as_15s_mss": True,
                    },
                    "target_pool": {
                        "pool_id": "15SH-target",
                        "timeframe": "15S",
                        "level": 104.6,
                        "confirmed_ts_ns": 1,
                    },
                }
            ],
        }
        signals = build_signals(
            report=report,
            upstream_report=report,
            instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        )
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.signal_kind, "PARENT_EXTERNAL_15S_MSS_5S_RETEST")
        self.assertGreater(signal.ts_event, signal.observed_time_ns)
        details = json.loads(signal.details_json)
        self.assertEqual(details["mss_timeframe"], "15S")
        self.assertEqual(details["retest_timeframe"], "5S")
        self.assertFalse(details["five_second_clock_selects_direction_or_boundary"])
        self.assertTrue(details["retest"]["same_boundary_as_15s_mss"])
        self.assertEqual(
            details["mss"]["boundary_pool_id"],
            details["retest"]["boundary_pool_id"],
        )
        serialized = signal.details_json.lower()
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
