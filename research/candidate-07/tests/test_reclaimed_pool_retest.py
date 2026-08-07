from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from diagnose_reclaimed_pool_retest import (
    ReclaimedPoolRetestLogic,
    diagnose,
)


class ReclaimedPoolRetestTests(unittest.TestCase):
    def _bars(self, *, directional_flow: bool = True) -> pd.DataFrame:
        base_second = 1_766_400_000  # divisible by 15
        count = 50
        timestamps = np.array(
            [
                (base_second + index) * 1_000_000_000 + 999_999_999
                for index in range(count)
            ],
            dtype=np.int64,
        )
        close = np.full(count, 99.0)
        close[:15] = 98.0  # completed pre-contact value bucket
        open_ = close.copy()
        high = np.maximum(open_, close) + 0.05
        low = np.minimum(open_, close) - 0.05

        # First post-event retest at index 22 and three completed seconds of
        # rejected-side confirmation.  The value target remains untouched until
        # after the entry observation at index 24.
        open_[22:25] = np.array([100.0, 99.8, 99.4])
        close[22:25] = np.array([99.8, 99.4, 99.0])
        high[22:25] = np.array([100.10, 99.85, 99.45])
        low[22:25] = np.array([98.95, 99.35, 98.95])
        low[25] = 97.90
        close[25] = 97.95
        open_[25] = 99.0
        high[25] = 99.05

        buy = np.full(count, 50.0)
        sell = np.full(count, 50.0)
        if directional_flow:
            sell[22:25] = 200.0
        else:
            buy[22:25] = 200.0
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
                "taker_buy_quote": buy,
                "taker_sell_quote": sell,
            }
        )

    def _detector(self, bars: pd.DataFrame) -> dict:
        timestamps = bars["timestamp_ns"].astype("int64")
        return {
            "scenarios": [
                {
                    "scenario_id": "detector-1",
                    "outcome": "EVENT_ACCEPTED",
                    "direction": "SHORT",
                    "inventory_state": "NEUTRAL",
                    "pool_id": "5MH-source",
                    "pool_side": "UPPER",
                    "liquidity_level": 100.0,
                    "contact": {
                        "timestamp_ns": int(timestamps.iloc[16]),
                        "atr": 1.0,
                    },
                    "recovery_terminal": {
                        "timestamp_ns": int(timestamps.iloc[20]),
                        "close": 99.0,
                    },
                    "entry_reference": 99.0,
                    "event_extreme": 100.8,
                    "stop": 101.0,
                    "risk": 2.0,
                    "recovery_quote_ratio": 0.4,
                    "impact_asymmetry": 3.0,
                }
            ]
        }

    def test_first_retest_rejection_enters_and_targets_value(self) -> None:
        bars = self._bars(directional_flow=True)
        result = diagnose(
            bars,
            detector_report=self._detector(bars),
            max_hold_seconds=20,
            logic=ReclaimedPoolRetestLogic(),
            require_flow_confirmation=True,
        )
        entries = [
            item
            for item in result["scenarios"]
            if item.get("outcome") == "ENTRY_READY"
        ]
        self.assertEqual(len(entries), 1, result)
        self.assertEqual(entries[0]["entry"], 99.0)
        self.assertEqual(entries[0]["target"], 98.0)
        self.assertEqual(entries[0]["path"]["outcome"], "TARGET")

    def test_flow_ablation_changes_only_flow_confirmation(self) -> None:
        bars = self._bars(directional_flow=False)
        baseline = diagnose(
            bars,
            detector_report=self._detector(bars),
            max_hold_seconds=20,
            logic=ReclaimedPoolRetestLogic(),
            require_flow_confirmation=True,
        )
        self.assertEqual(baseline["summary"]["entry_ready"], 0)
        ablation = diagnose(
            bars,
            detector_report=self._detector(bars),
            max_hold_seconds=20,
            logic=ReclaimedPoolRetestLogic(),
            require_flow_confirmation=False,
        )
        self.assertEqual(ablation["summary"]["entry_ready"], 1, ablation)

    def test_value_delivery_before_retest_cancels_scenario(self) -> None:
        bars = self._bars(directional_flow=True)
        bars.loc[21, "low"] = 97.9
        result = diagnose(
            bars,
            detector_report=self._detector(bars),
            max_hold_seconds=20,
            logic=ReclaimedPoolRetestLogic(),
            require_flow_confirmation=True,
        )
        self.assertEqual(result["summary"]["entry_ready"], 0)
        self.assertEqual(
            result["summary"]["contact_counts"][
                "VALUE_DELIVERED_BEFORE_RETEST"
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()
