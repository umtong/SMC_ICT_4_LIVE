from __future__ import annotations

import json
import unittest

import pandas as pd

from nautilus_trader.model.identifiers import InstrumentId

import diagnose_impact_resilience_1s as impact
from run_local_liquidity_sweep_mss_retest import (
    LocalSweepMSSLogic,
    _break_retest_index,
    _mss_index,
    _sweep_direction,
    build_causal_signals,
)


class LocalLiquiditySweepMSSRetestTests(unittest.TestCase):
    @staticmethod
    def _logic() -> LocalSweepMSSLogic:
        return LocalSweepMSSLogic(
            atr_history_bars=2,
            reference_history_bars=2,
            mss_context_bars=8,
            maximum_mss_bars=3,
            maximum_retest_bars=3,
        )

    @staticmethod
    def _event_row() -> pd.Series:
        return pd.Series(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.8,
                "close": 99.9,
                "range": 1.2,
                "atr": 1.0,
                "signed_quote": 8_000.0,
                "quote_volume": 10_000.0,
                "imbalance": 0.8,
                "signed_flow_reference": 500.0,
                "quote_volume_reference": 5_000.0,
                "imbalance_reference": 0.3,
                "price_efficiency": 1.0 / 12.0,
            }
        )

    def test_upper_first_touch_buy_failure_is_short_sweep(self) -> None:
        pool = impact.Pool(
            pool_id="15SH-1",
            timeframe="15S",
            side="UPPER",
            level=100.5,
            pivot_ts_ns=1,
            confirmed_ts_ns=2,
        )
        self.assertEqual(
            _sweep_direction(self._event_row(), pool, self._logic()),
            "SHORT",
        )

    def test_attack_without_reclaim_is_not_a_sweep(self) -> None:
        row = self._event_row()
        row["close"] = 100.6
        pool = impact.Pool(
            pool_id="15SH-1",
            timeframe="15S",
            side="UPPER",
            level=100.5,
            pivot_ts_ns=1,
            confirmed_ts_ns=2,
        )
        self.assertIsNone(_sweep_direction(row, pool, self._logic()))

    @staticmethod
    def _path_bars() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "timestamp_ns": 14_999_999_999,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.8,
                    "close": 99.9,
                    "range": 1.2,
                    "body": -0.1,
                    "body_atr": 0.1,
                    "body_reference": 0.2,
                    "close_location": 0.0833,
                    "imbalance": 0.8,
                    "signed_quote": 8_000.0,
                },
                {
                    "timestamp_ns": 29_999_999_999,
                    "open": 99.9,
                    "high": 100.0,
                    "low": 99.2,
                    "close": 99.3,
                    "range": 0.8,
                    "body": -0.6,
                    "body_atr": 0.6,
                    "body_reference": 0.2,
                    "close_location": 0.125,
                    "imbalance": -0.4,
                    "signed_quote": -3_000.0,
                },
                {
                    "timestamp_ns": 44_999_999_999,
                    "open": 99.55,
                    "high": 99.65,
                    "low": 99.2,
                    "close": 99.3,
                    "range": 0.45,
                    "body": -0.25,
                    "body_atr": 0.25,
                    "body_reference": 0.2,
                    "close_location": 0.2222,
                    "imbalance": -0.25,
                    "signed_quote": -1_500.0,
                },
            ]
        )

    def test_short_mss_then_broken_swing_retest_is_causal(self) -> None:
        bars = self._path_bars()
        boundary = impact.Pool(
            pool_id="15SL-boundary",
            timeframe="15S",
            side="LOWER",
            level=99.5,
            pivot_ts_ns=1,
            confirmed_ts_ns=2,
        )
        mss, reason = _mss_index(
            bars,
            contact_index=0,
            direction="SHORT",
            boundary=boundary,
            event_extreme=101.0,
            event_atr=1.0,
            logic=self._logic(),
        )
        self.assertEqual(reason, "MSS_CONFIRMED")
        self.assertEqual(mss, 1)
        retest, reason = _break_retest_index(
            bars,
            mss_index=mss,
            direction="SHORT",
            boundary_level=boundary.level,
            event_extreme=101.0,
            event_atr=1.0,
            logic=self._logic(),
        )
        self.assertEqual(reason, "BREAK_RETEST_CONFIRMED")
        self.assertEqual(retest, 2)

    def test_source_extreme_rebreak_invalidates_before_mss(self) -> None:
        bars = self._path_bars()
        bars.loc[1, "high"] = 101.1
        boundary = impact.Pool(
            pool_id="15SL-boundary",
            timeframe="15S",
            side="LOWER",
            level=99.5,
            pivot_ts_ns=1,
            confirmed_ts_ns=2,
        )
        mss, reason = _mss_index(
            bars,
            contact_index=0,
            direction="SHORT",
            boundary=boundary,
            event_extreme=101.0,
            event_atr=1.0,
            logic=self._logic(),
        )
        self.assertIsNone(mss)
        self.assertEqual(reason, "SOURCE_INVALIDATED_BEFORE_MSS")

    def test_signal_has_no_future_path_and_delivers_after_completed_observation(self) -> None:
        observed = 44_999_999_999
        report = {
            "summary": {"require_retest": True},
            "scenarios": [
                {
                    "scenario_id": "local-sweep-1",
                    "outcome": "ENTRY_READY",
                    "direction": "SHORT",
                    "entry": 99.3,
                    "stop": 101.05,
                    "target": 96.0,
                    "expected_rr": 1.8857,
                    "source_pool_id": "15SH-source",
                    "observed_time_ns": observed,
                    "sweep": {"timestamp_ns": 14_999_999_999},
                    "mss": {"timestamp_ns": 29_999_999_999},
                    "retest": {"timestamp_ns": observed},
                    "target_pool": {
                        "pool_id": "15SL-target",
                        "timeframe": "15S",
                        "level": 96.0,
                        "confirmed_ts_ns": 1,
                    },
                }
            ],
        }
        signals = build_causal_signals(
            report=report,
            upstream_report={},
            instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].observed_time_ns, observed)
        self.assertEqual(signals[0].ts_event, observed + 1)
        serialized = json.dumps(json.loads(signals[0].details_json)).lower()
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
