from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from diagnose_pre_attack_value import PreAttackValueLogic, diagnose


class PreAttackValueTests(unittest.TestCase):
    def _bars(self) -> pd.DataFrame:
        base_second = 1_766_400_000
        count = 45
        timestamps = np.array(
            [
                (base_second + index) * 1_000_000_000 + 999_999_999
                for index in range(count)
            ],
            dtype=np.int64,
        )
        close = np.full(count, 100.0)
        close[:14] = 98.0
        close[14] = 97.0
        close[21] = 99.0
        # The future path must actually trade through both the prior-bucket
        # VWAP and the lower close-ablation target.  This is deliberately after
        # the causal entry observation and does not affect target construction.
        close[22] = 96.8
        open_ = close.copy()
        high = np.maximum(open_, close) + 0.1
        low = np.minimum(open_, close) - 0.1
        volume = np.ones(count)
        quote = close * volume
        return pd.DataFrame(
            {
                "timestamp_ns": timestamps,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "quote_volume": quote,
            }
        )

    def _upstream(self, bars: pd.DataFrame) -> dict:
        # Read nanoseconds from the integer column directly. Selecting an entire
        # mixed numeric row can coerce int64 to float64 and lose sub-second bits.
        timestamps = bars["timestamp_ns"].astype("int64")
        contact_ns = int(timestamps.iloc[16])
        recovery_ns = int(timestamps.iloc[20])
        self.assertEqual(
            recovery_ns % 1_000_000_000,
            999_999_999,
        )
        return {
            "scenarios": [
                {
                    "scenario_id": "upstream-1",
                    "outcome": "ENTRY_READY",
                    "direction": "SHORT",
                    "inventory_state": "NEUTRAL",
                    "contact": {
                        "timestamp_ns": contact_ns,
                    },
                    "recovery_terminal": {
                        "timestamp_ns": recovery_ns,
                    },
                    "entry": 100.0,
                    "stop": 101.0,
                    "recovery_quote_ratio": 0.5,
                    "impact_asymmetry": 2.0,
                }
            ]
        }

    def test_prior_complete_bucket_vwap_is_causal_target(self) -> None:
        bars = self._bars()
        result = diagnose(
            bars,
            upstream_report=self._upstream(bars),
            max_hold_seconds=20,
            logic=PreAttackValueLogic(target_statistic="vwap"),
        )
        entries = [
            item
            for item in result["scenarios"]
            if item.get("outcome") == "ENTRY_READY"
        ]
        self.assertEqual(len(entries), 1)
        expected_vwap = (14 * 98.0 + 97.0) / 15.0
        self.assertAlmostEqual(entries[0]["target"], expected_vwap)
        self.assertAlmostEqual(entries[0]["target_rr"], 100.0 - expected_vwap)
        self.assertEqual(entries[0]["path"]["outcome"], "TARGET")

    def test_close_ablation_removes_only_volume_weighting(self) -> None:
        bars = self._bars()
        result = diagnose(
            bars,
            upstream_report=self._upstream(bars),
            max_hold_seconds=20,
            logic=PreAttackValueLogic(target_statistic="close"),
        )
        entry = next(
            item
            for item in result["scenarios"]
            if item.get("outcome") == "ENTRY_READY"
        )
        self.assertEqual(entry["target"], 97.0)
        self.assertEqual(entry["target_details"]["target_statistic"], "close")
        self.assertEqual(entry["path"]["outcome"], "TARGET")

    def test_invalid_target_statistic_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PreAttackValueLogic(target_statistic="future").validate()


if __name__ == "__main__":
    unittest.main()
