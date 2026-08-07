from __future__ import annotations

import json
import unittest

import numpy as np
import pandas as pd

from nautilus_trader.model.identifiers import InstrumentId

import diagnose_impact_resilience_1s as impact
import run_local_liquidity_sweep_mss_retest as local
from five_second_flow_bars import (
    _latest_five_second_boundary,
    _prepare_five_second_bars,
    same_wall_clock_second_index,
    scaled_execution_logic,
)
from multiclock_sweep_mss_scenario import build_five_second_signals


class MulticlockSweepMSSRetestTests(unittest.TestCase):
    @staticmethod
    def _logic() -> local.LocalSweepMSSLogic:
        return local.LocalSweepMSSLogic(
            atr_history_bars=2,
            reference_history_bars=2,
            mss_context_bars=4,
            maximum_mss_bars=2,
            maximum_retest_bars=2,
        )

    @staticmethod
    def _seconds(bucket_count: int = 8) -> pd.DataFrame:
        rows = []
        price = 100.0
        for bucket in range(bucket_count):
            for second in range(5):
                timestamp_ns = (bucket * 5 + second + 1) * 1_000_000_000 - 1
                close = price + 0.01 * ((second % 3) - 1)
                rows.append(
                    {
                        "timestamp_ns": timestamp_ns,
                        "open": price,
                        "high": max(price, close) + 0.01,
                        "low": min(price, close) - 0.01,
                        "close": close,
                        "volume": 1.0,
                        "quote_volume": close,
                        "taker_buy_quote": close * 0.55,
                        "taker_sell_quote": close * 0.45,
                    }
                )
                price = close
        return pd.DataFrame(rows)

    def test_five_second_aggregation_is_complete_and_wall_clock_scaled(self) -> None:
        logic = scaled_execution_logic(self._logic())
        bars = _prepare_five_second_bars(self._seconds(), logic)
        self.assertEqual(len(bars.index), 8)
        self.assertEqual(int(bars.iloc[0]["timestamp_ns"]), 4_999_999_999)
        self.assertEqual(logic.maximum_mss_bars, 6)
        self.assertEqual(logic.maximum_retest_bars, 6)
        self.assertEqual(logic.mss_context_bars, 12)

    def test_prior_references_do_not_change_when_future_flow_changes(self) -> None:
        logic = scaled_execution_logic(self._logic())
        seconds = self._seconds(10)
        original = _prepare_five_second_bars(seconds, logic)
        mutated = seconds.copy()
        cutoff = int(original.iloc[6]["timestamp_ns"])
        mask = mutated["timestamp_ns"] > cutoff
        mutated.loc[mask, "quote_volume"] *= 100.0
        mutated.loc[mask, "taker_buy_quote"] *= 100.0
        mutated.loc[mask, "taker_sell_quote"] *= 100.0
        changed = _prepare_five_second_bars(mutated, logic)
        for name in (
            "atr",
            "signed_flow_reference",
            "quote_volume_reference",
            "imbalance_reference",
            "body_reference",
        ):
            np.testing.assert_allclose(
                original.loc[:6, name].to_numpy(dtype=float),
                changed.loc[:6, name].to_numpy(dtype=float),
                equal_nan=True,
            )

    def test_completed_second_alignment_ignores_endpoint_precision(self) -> None:
        timestamps = np.array(
            [
                4_999_999_999,
                9_999_999_999,
                14_999_999_999,
            ],
            dtype=np.int64,
        )
        # The 15-second source may encode the same completed second at 999 ms.
        self.assertEqual(
            same_wall_clock_second_index(timestamps, 14_999_000_000),
            2,
        )
        self.assertIsNone(
            same_wall_clock_second_index(timestamps, 15_999_000_000)
        )

    def test_boundary_must_be_confirmed_before_sweep_bar_begins(self) -> None:
        pools = [
            impact.Pool("5SH-old", "5S", "UPPER", 101.0, 10, 20),
            impact.Pool("5SH-new", "5S", "UPPER", 100.8, 30, 40),
            impact.Pool("5SH-inside", "5S", "UPPER", 100.7, 50, 120),
        ]
        selected = _latest_five_second_boundary(
            pools,
            direction="LONG",
            event_start_ns=100,
            event_close=100.0,
            source_pivot_ns=999,
            context_ns=100,
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.pool_id, "5SH-new")

    def test_signal_is_delivered_after_completed_five_second_retest(self) -> None:
        observed = 44_999_999_999
        report = {
            "summary": {"require_retest": True},
            "scenarios": [
                {
                    "scenario_id": "multiclock-1",
                    "outcome": "ENTRY_READY",
                    "direction": "SHORT",
                    "entry": 99.0,
                    "stop": 100.0,
                    "target": 97.0,
                    "expected_rr": 2.0,
                    "source_pool_id": "15SH-source",
                    "observed_time_ns": observed,
                    "sweep": {"timestamp_ns": 14_999_999_999},
                    "mss": {
                        "execution_timeframe": "5S",
                        "timestamp_ns": 29_999_999_999,
                    },
                    "retest": {
                        "execution_timeframe": "5S",
                        "timestamp_ns": observed,
                    },
                    "target_pool": {
                        "pool_id": "15SL-target",
                        "timeframe": "15S",
                        "level": 97.0,
                        "confirmed_ts_ns": 1,
                    },
                }
            ],
        }
        signals = build_five_second_signals(
            report=report,
            upstream_report={},
            instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].observed_time_ns, observed)
        self.assertEqual(signals[0].ts_event, observed + 1)
        details = json.loads(signals[0].details_json)
        self.assertEqual(details["execution_timeframe"], "5S")
        serialized = json.dumps(details).lower()
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
