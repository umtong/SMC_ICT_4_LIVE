from __future__ import annotations

import json
import unittest

import pandas as pd

from nautilus_trader.model.identifiers import InstrumentId

import diagnose_impact_resilience_1s as impact
from nested_liquidity_sweep import (
    _aggregate_thirty_seconds,
    _independent_boundary,
    _source_first_touches,
)
from nested_liquidity_sweep_scenario import build_causal_signals


class NestedLiquiditySweepMSSRetestTests(unittest.TestCase):
    def test_thirty_second_aggregation_uses_two_complete_bars(self) -> None:
        bars = pd.DataFrame(
            [
                {
                    "timestamp_ns": 14_999_999_999,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.5,
                    "close": 100.5,
                },
                {
                    "timestamp_ns": 29_999_999_999,
                    "open": 100.5,
                    "high": 102.0,
                    "low": 100.0,
                    "close": 101.5,
                },
                {
                    "timestamp_ns": 44_999_999_999,
                    "open": 101.5,
                    "high": 103.0,
                    "low": 101.0,
                    "close": 102.0,
                },
            ]
        )
        result = _aggregate_thirty_seconds(bars)
        self.assertEqual(len(result.index), 1)
        row = result.iloc[0]
        self.assertEqual(int(row["timestamp_ns"]), 29_999_999_999)
        self.assertEqual(float(row["open"]), 100.0)
        self.assertEqual(float(row["high"]), 102.0)
        self.assertEqual(float(row["low"]), 99.5)
        self.assertEqual(float(row["close"]), 101.5)

    def test_same_side_collision_selects_highest_source_timeframe(self) -> None:
        bars = pd.DataFrame(
            [
                {
                    "timestamp_ns": 14_999_999_999,
                    "open": 99.0,
                    "high": 99.5,
                    "low": 98.5,
                    "close": 99.0,
                },
                {
                    "timestamp_ns": 29_999_999_999,
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.9,
                    "close": 100.0,
                },
            ]
        )
        pools = [
            impact.Pool("15SH-a", "15S", "UPPER", 100.0, 1, 1),
            impact.Pool("30SH-a", "30S", "UPPER", 100.2, 2, 1),
            impact.Pool("1MH-a", "1M", "UPPER", 100.4, 3, 1),
        ]
        selected, summary = _source_first_touches(bars, pools)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0][1].timeframe, "1M")
        self.assertEqual(summary["selected_source_counts"], {"1M": 1})

    def test_source_and_mss_pivots_must_be_distinct(self) -> None:
        source = impact.Pool("15SL-a", "15S", "LOWER", 99.0, 10, 20)
        same_bar = impact.Pool("15SH-a", "15S", "UPPER", 101.0, 10, 20)
        independent = impact.Pool("15SH-b", "15S", "UPPER", 101.0, 11, 21)
        self.assertFalse(_independent_boundary(source, same_bar))
        self.assertTrue(_independent_boundary(source, independent))
        self.assertFalse(_independent_boundary(source, None))

    def test_signal_has_no_future_path_and_is_delivered_after_retest(self) -> None:
        observed = 44_999_999_999
        report = {
            "summary": {
                "require_retest": True,
                "source_timeframes": ["15S", "30S", "1M"],
            },
            "scenarios": [
                {
                    "scenario_id": "nested-1",
                    "outcome": "ENTRY_READY",
                    "direction": "LONG",
                    "entry": 100.0,
                    "stop": 99.0,
                    "target": 102.0,
                    "expected_rr": 2.0,
                    "source_pool_id": "1ML-a",
                    "observed_time_ns": observed,
                    "sweep": {"timestamp_ns": 14_999_999_999},
                    "mss": {"timestamp_ns": 29_999_999_999},
                    "retest": {"timestamp_ns": observed},
                    "target_pool": {
                        "pool_id": "30SH-target",
                        "timeframe": "30S",
                        "level": 102.0,
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
        details = json.loads(signals[0].details_json)
        self.assertEqual(details["source_timeframes"], ["15S", "30S", "1M"])
        serialized = json.dumps(details).lower()
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
