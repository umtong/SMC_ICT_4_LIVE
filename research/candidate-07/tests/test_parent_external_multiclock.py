from __future__ import annotations

import json
import unittest

import pandas as pd

from diagnose_impact_resilience_1s import Pool
from nautilus_trader.model.identifiers import InstrumentId
from parent_external_multiclock_scenario import (
    build_parent_ensemble_signals,
    parent_source_first_touches,
)


class ParentExternalFirstTouchTests(unittest.TestCase):
    @staticmethod
    def _bars() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "timestamp_ns": [
                    4_999_999_999,
                    9_999_999_999,
                    14_999_999_999,
                ],
                "high": [99.0, 101.5, 102.0],
                "low": [98.0, 98.5, 99.0],
                "close": [98.5, 100.5, 101.0],
            }
        )

    @staticmethod
    def _pool(
        *,
        pool_id: str,
        timeframe: str,
        side: str,
        level: float,
        pivot_ns: int,
    ) -> Pool:
        return Pool(
            pool_id=pool_id,
            timeframe=timeframe,
            side=side,
            level=level,
            pivot_ts_ns=pivot_ns,
            confirmed_ts_ns=4_000_000_000,
        )

    def test_same_side_collision_prefers_five_minute_parent(self) -> None:
        selected, summary = parent_source_first_touches(
            self._bars(),
            [
                self._pool(
                    pool_id="1MH-one",
                    timeframe="1M",
                    side="UPPER",
                    level=100.0,
                    pivot_ns=1,
                ),
                self._pool(
                    pool_id="5MH-five",
                    timeframe="5M",
                    side="UPPER",
                    level=101.0,
                    pivot_ns=2,
                ),
            ],
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0][0], 1)
        self.assertEqual(selected[0][1].pool_id, "5MH-five")
        self.assertEqual(summary["selected_source_counts"], {"5M": 1})
        self.assertEqual(summary["same_side_parent_collision_bars"], 1)
        self.assertEqual(summary["same_side_extra_parent_pools_consumed"], 1)

    def test_opposite_side_collision_is_consumed_without_scenario(self) -> None:
        bars = self._bars().copy()
        bars.loc[1, "low"] = 97.0
        selected, summary = parent_source_first_touches(
            bars,
            [
                self._pool(
                    pool_id="1MH-upper",
                    timeframe="1M",
                    side="UPPER",
                    level=100.0,
                    pivot_ns=1,
                ),
                self._pool(
                    pool_id="5ML-lower",
                    timeframe="5M",
                    side="LOWER",
                    level=98.0,
                    pivot_ns=2,
                ),
            ],
        )
        self.assertEqual(selected, [])
        self.assertEqual(summary["opposite_side_ambiguous_touch_bars"], 1)
        self.assertEqual(summary["opposite_side_pools_consumed"], 2)

    def test_local_fifteen_second_pool_cannot_be_parent_source(self) -> None:
        with self.assertRaises(ValueError):
            parent_source_first_touches(
                self._bars(),
                [
                    self._pool(
                        pool_id="15SH-local",
                        timeframe="15S",
                        side="UPPER",
                        level=100.0,
                        pivot_ns=1,
                    )
                ],
            )


class ParentExternalSignalTests(unittest.TestCase):
    def test_signal_preserves_parent_source_and_clock_arbitration(self) -> None:
        observed_ns = 20_000_000_000
        report = {
            "summary": {"require_retest": True},
            "scenarios": [
                {
                    "scenario_id": "parent-one",
                    "outcome": "ENTRY_READY",
                    "direction": "SHORT",
                    "entry": 100.0,
                    "stop": 101.0,
                    "target": 98.0,
                    "expected_rr": 2.0,
                    "source_pool_id": "5MH-parent",
                    "source_timeframe": "5M",
                    "execution_timeframe": "5S",
                    "observed_time_ns": observed_ns,
                    "episode_key": {
                        "source_pool_id": "5MH-parent",
                        "sweep_timestamp_ns": 10_000_000_000,
                        "direction": "SHORT",
                    },
                    "episode_selection": "FIRST_COMPLETED_VALID_RETEST",
                    "sweep": {
                        "timestamp_ns": 10_000_000_000,
                        "pool_id": "5MH-parent",
                        "pool_side": "UPPER",
                        "pool_level": 100.5,
                        "pool_pivot_ts_ns": 1_000_000_000,
                        "pool_confirmed_ts_ns": 5_000_000_000,
                        "source_timeframe": "5M",
                        "open": 100.0,
                        "high": 100.7,
                        "low": 99.9,
                        "close": 100.2,
                        "atr": 1.0,
                        "event_extreme": 100.7,
                        "signed_quote": 10.0,
                        "imbalance": 0.2,
                        "quote_volume": 100.0,
                    },
                    "mss": {
                        "timestamp_ns": 15_000_000_000,
                        "boundary_pool_id": "5SL-boundary",
                        "boundary_level": 99.8,
                        "boundary_confirmed_ts_ns": 9_000_000_000,
                        "close": 99.7,
                        "body_atr": 0.5,
                        "imbalance": -0.1,
                    },
                    "retest": {
                        "timestamp_ns": observed_ns,
                        "boundary_level": 99.8,
                        "close": 100.0,
                        "imbalance": -0.1,
                    },
                    "target_pool": {
                        "pool_id": "15SL-target",
                        "timeframe": "15S",
                        "level": 98.0,
                        "confirmed_ts_ns": 8_000_000_000,
                    },
                }
            ],
        }
        signals = build_parent_ensemble_signals(
            report=report,
            upstream_report=report,
            instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        )
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.signal_kind, "PARENT_EXTERNAL_MULTICLOCK_FIRST_RETEST")
        self.assertEqual(signal.source_pool_id, "5MH-parent")
        self.assertGreater(signal.ts_event, signal.observed_time_ns)
        details = json.loads(signal.details_json)
        self.assertEqual(details["source_timeframe"], "5M")
        self.assertEqual(details["source_scope"], "parent_external_liquidity")
        self.assertEqual(details["execution_timeframe"], "5S")
        self.assertEqual(
            details["episode_selection"],
            "FIRST_COMPLETED_VALID_RETEST",
        )
        serialized = signal.details_json.lower()
        for forbidden in ("mfe", "mae", "realized_r", "terminal", "path"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
