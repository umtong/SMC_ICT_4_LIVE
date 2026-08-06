from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from diagnose_aggtrade_volume_time import VolumeTimeLogic, diagnose
from diagnose_impact_resilience_1s import Pool


class VolumeTimeStateMachineTests(unittest.TestCase):
    def _bars(self, recovery_sell_quote: float) -> pd.DataFrame:
        base_second = 1_766_400_000
        count = 12
        timestamps = np.array(
            [
                (base_second + index) * 1_000_000_000 + 999_999_999
                for index in range(count)
            ],
            dtype=np.int64,
        )
        opens = np.array(
            [
                99.0,
                99.0,
                99.1,
                100.0,
                99.8,
                97.5,
                97.6,
                94.2,
                94.3,
                94.4,
                94.5,
                94.6,
            ]
        )
        closes = np.array(
            [
                99.0,
                99.1,
                99.0,
                99.95,
                97.6,
                97.55,
                94.1,
                94.25,
                94.35,
                94.45,
                94.55,
                94.65,
            ]
        )
        highs = np.maximum(opens, closes) + 0.05
        lows = np.minimum(opens, closes) - 0.05
        highs[1] = 100.15
        highs[2] = 100.20
        lows[4] = 97.40
        lows[6] = 93.90

        buy = np.full(count, 50.0)
        sell = np.full(count, 50.0)
        buy[1] = 600.0
        buy[2] = 500.0
        sell[3:6] = recovery_sell_quote / 3.0
        quote = buy + sell
        return pd.DataFrame(
            {
                "timestamp_ns": timestamps,
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "quote_volume": quote,
                "taker_buy_quote": buy,
                "taker_sell_quote": sell,
                "signed_quote": buy - sell,
                "trade_count": np.full(count, 10, dtype=np.int64),
                "atr": np.ones(count),
                "positioning_valid": np.ones(count, dtype=bool),
                "inventory_state": np.full(count, "RELEASE", dtype=object),
                "sum_open_interest": np.full(count, 1000.0),
                "oi_change_fraction": np.full(count, -0.001),
                "buy_q": np.full(count, 1000.0),
                "sell_q": np.full(count, 1000.0),
            }
        )

    def _pools(self) -> tuple[Pool, Pool]:
        base_second = 1_766_400_000
        confirmation = (
            (base_second - 2) * 1_000_000_000 + 999_000_000
        )
        source = Pool(
            "5MH-source",
            "5M",
            "UPPER",
            100.0,
            confirmation - 300_000_000_000,
            confirmation,
        )
        target = Pool(
            "1ML-target",
            "1M",
            "LOWER",
            94.0,
            confirmation - 60_000_000_000,
            confirmation,
        )
        return source, target

    def _diagnose(self, bars: pd.DataFrame) -> dict:
        source, target = self._pools()
        return diagnose(
            bars,
            source_pools=[source],
            target_pools={"1M": [target], "5M": []},
            trade_start_ns=1_766_400_000_000_000_000,
            trade_end_ns=1_766_400_020_000_000_000,
            max_hold_seconds=8,
            logic=VolumeTimeLogic(),
            require_oi_release=True,
        )

    def test_less_counter_aggression_can_confirm_full_reclaim(self) -> None:
        result = self._diagnose(self._bars(recovery_sell_quote=300.0))
        entries = [
            item
            for item in result["scenarios"]
            if item.get("outcome") == "ENTRY_READY"
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["direction"], "SHORT")
        self.assertEqual(entries[0]["recovery_seconds"], 3)
        self.assertLess(entries[0]["recovery_quote_ratio"], 1.0)
        self.assertEqual(entries[0]["path"]["outcome"], "TARGET")

    def test_one_recovery_second_cannot_complete_terminal_window(self) -> None:
        bars = self._bars(recovery_sell_quote=300.0)
        bars.loc[4:, "timestamp_ns"] += 1_000_000_000
        result = self._diagnose(bars)
        self.assertEqual(result["summary"]["entry_ready"], 0)
        self.assertEqual(
            result["summary"]["contact_counts"][
                "RECOVERY_NOT_CONFIRMED_WITHIN_HORIZON"
            ],
            1,
        )

    def test_reclaim_after_larger_counter_budget_is_not_same_alpha(self) -> None:
        result = self._diagnose(self._bars(recovery_sell_quote=1_200.0))
        self.assertEqual(result["summary"]["entry_ready"], 0)
        self.assertEqual(
            result["summary"]["contact_counts"][
                "RECOVERY_USED_MORE_OPPOSITE_QUOTE_THAN_ATTACK"
            ],
            1,
        )

    def test_invalid_horizon_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            VolumeTimeLogic(
                maximum_recovery_seconds=2,
                terminal_seconds=3,
            ).validate()


if __name__ == "__main__":
    unittest.main()
