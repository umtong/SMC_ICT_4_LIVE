from __future__ import annotations

import json
import unittest

import numpy as np
import pandas as pd

from nautilus_trader.model.identifiers import InstrumentId

from run_global_flow_absorption import (
    GlobalAbsorptionLogic,
    _aggregate_fifteen_seconds,
    _confirmation,
    _event_direction,
    _retest,
    build_causal_signals,
)


class GlobalFlowAbsorptionTests(unittest.TestCase):
    @staticmethod
    def _logic() -> GlobalAbsorptionLogic:
        return GlobalAbsorptionLogic(
            atr_history_bars=2,
            reference_history_bars=2,
            signed_flow_quantile=0.75,
            quote_volume_quantile=0.50,
            imbalance_quantile=0.50,
            confirmation_bars=2,
            retest_bars=2,
        )

    @staticmethod
    def _bars() -> pd.DataFrame:
        rows = [
            {
                "timestamp_ns": 14_999_999_999,
                "open": 100.0,
                "high": 100.2,
                "low": 99.8,
                "close": 100.0,
                "volume": 10.0,
                "quote_volume": 1_000.0,
                "taker_buy_quote": 550.0,
                "taker_sell_quote": 450.0,
                "signed_quote": 100.0,
                "imbalance": 0.10,
                "vwap": 100.0,
                "range": 0.4,
                "price_efficiency": 0.0,
                "atr": 1.0,
                "signed_flow_reference": 500.0,
                "quote_volume_reference": 5_000.0,
                "imbalance_reference": 0.30,
            },
            {
                "timestamp_ns": 29_999_999_999,
                "open": 100.0,
                "high": 101.0,
                "low": 99.8,
                "close": 100.2,
                "volume": 100.0,
                "quote_volume": 10_000.0,
                "taker_buy_quote": 9_000.0,
                "taker_sell_quote": 1_000.0,
                "signed_quote": 8_000.0,
                "imbalance": 0.80,
                "vwap": 100.5,
                "range": 1.2,
                "price_efficiency": 1.0 / 6.0,
                "atr": 1.0,
                "signed_flow_reference": 500.0,
                "quote_volume_reference": 5_000.0,
                "imbalance_reference": 0.30,
            },
            {
                "timestamp_ns": 44_999_999_999,
                "open": 100.2,
                "high": 100.3,
                "low": 99.5,
                "close": 99.7,
                "volume": 50.0,
                "quote_volume": 5_000.0,
                "taker_buy_quote": 1_500.0,
                "taker_sell_quote": 3_500.0,
                "signed_quote": -2_000.0,
                "imbalance": -0.40,
                "vwap": 99.9,
                "range": 0.8,
                "price_efficiency": 0.625,
                "atr": 1.0,
                "signed_flow_reference": 500.0,
                "quote_volume_reference": 5_000.0,
                "imbalance_reference": 0.30,
            },
            {
                "timestamp_ns": 59_999_999_999,
                "open": 99.8,
                "high": 100.6,
                "low": 99.6,
                "close": 99.7,
                "volume": 45.0,
                "quote_volume": 4_500.0,
                "taker_buy_quote": 1_500.0,
                "taker_sell_quote": 3_000.0,
                "signed_quote": -1_500.0,
                "imbalance": -1.0 / 3.0,
                "vwap": 100.0,
                "range": 1.0,
                "price_efficiency": 0.1,
                "atr": 1.0,
                "signed_flow_reference": 500.0,
                "quote_volume_reference": 5_000.0,
                "imbalance_reference": 0.30,
            },
        ]
        return pd.DataFrame(rows)

    def test_failed_buy_aggression_routes_short_after_recovery_and_retest(self) -> None:
        logic = self._logic()
        bars = self._bars()
        self.assertEqual(_event_direction(bars.iloc[1], logic), "SHORT")
        confirmation = _confirmation(
            bars,
            event_index=1,
            direction="SHORT",
            logic=logic,
        )
        self.assertEqual(confirmation, 2)
        retest = _retest(
            bars,
            event_index=1,
            confirmation_index=confirmation,
            direction="SHORT",
            logic=logic,
        )
        self.assertEqual(retest, 3)

    def test_source_invalidation_prevents_confirmation(self) -> None:
        logic = self._logic()
        bars = self._bars()
        bars.loc[2, "high"] = 101.2
        self.assertIsNone(
            _confirmation(
                bars,
                event_index=1,
                direction="SHORT",
                logic=logic,
            )
        )

    @staticmethod
    def _seconds(bucket_count: int = 8) -> pd.DataFrame:
        rows = []
        price = 100.0
        for bucket in range(bucket_count):
            for second in range(15):
                timestamp_ns = (
                    (bucket * 15 + second + 1) * 1_000_000_000 - 1
                )
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

    def test_rolling_references_use_only_prior_completed_bars(self) -> None:
        logic = self._logic()
        original_seconds = self._seconds()
        original = _aggregate_fifteen_seconds(original_seconds, logic)
        mutated_seconds = original_seconds.copy()
        cutoff = int(original.iloc[5]["timestamp_ns"])
        future = mutated_seconds["timestamp_ns"] > cutoff
        mutated_seconds.loc[future, "quote_volume"] *= 100.0
        mutated_seconds.loc[future, "taker_buy_quote"] *= 100.0
        mutated_seconds.loc[future, "taker_sell_quote"] *= 100.0
        mutated = _aggregate_fifteen_seconds(mutated_seconds, logic)
        for name in (
            "atr",
            "signed_flow_reference",
            "quote_volume_reference",
            "imbalance_reference",
        ):
            left = original.loc[:5, name].to_numpy(dtype=float)
            right = mutated.loc[:5, name].to_numpy(dtype=float)
            np.testing.assert_allclose(left, right, equal_nan=True)

    def test_signal_contains_only_observed_state_and_delivers_after_observation(self) -> None:
        observed = 44_999_999_999
        report = {
            "summary": {"require_retest": True},
            "scenarios": [
                {
                    "scenario_id": "gfa-1",
                    "outcome": "ENTRY_READY",
                    "direction": "SHORT",
                    "entry": 99.7,
                    "stop": 101.05,
                    "target": 97.0,
                    "expected_rr": 2.0,
                    "source_pool_id": "1ML-test",
                    "observed_time_ns": observed,
                    "event": {"timestamp_ns": 29_999_999_999},
                    "confirmation": {"timestamp_ns": observed},
                    "retest": {"timestamp_ns": observed},
                    "target_pool": {
                        "pool_id": "1ML-test",
                        "timeframe": "1M",
                        "level": 97.0,
                        "confirmed_ts_ns": 10_000_000_000,
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
        serialized = json.dumps(details).lower()
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
