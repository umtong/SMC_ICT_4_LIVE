from __future__ import annotations

import unittest

import pandas as pd

import cross_market_follower_failure_reversal_compiler as candidate


class FollowerExpansionTests(unittest.TestCase):
    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "open": [100.0, 100.0],
                "high": [101.0, 103.0],
                "low": [99.0, 99.5],
                "close": [100.0, 102.0],
                "ret_60s_bps": [0.0, 20.0],
                "flow_60s": [0.0, 0.60],
                "basis_change_5m": [0.0, 1.0],
                "notional_60s": [1_000.0, 2_000.0],
            }
        )

    def thresholds(self) -> dict[str, pd.Series]:
        return {
            "return": pd.Series([10.0, 10.0]),
            "flow": pd.Series([0.40, 0.40]),
        }

    def test_tail_return_flow_and_basis_define_expansion(self) -> None:
        passed, details = candidate.follower_expansion(
            self.frame(),
            1,
            1,
            self.thresholds(),
        )
        self.assertTrue(passed)
        self.assertEqual(details["follower_expansion_open"], 100.0)
        self.assertGreater(
            details["follower_directional_return_60s_bps"],
            details["follower_return_cutoff"],
        )

    def test_wrong_direction_basis_rejects_expansion(self) -> None:
        frame = self.frame()
        frame.loc[1, "basis_change_5m"] = -1.0
        passed, _ = candidate.follower_expansion(
            frame,
            1,
            1,
            self.thresholds(),
        )
        self.assertFalse(passed)


class ExpansionFailureTests(unittest.TestCase):
    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "close": [102.0, 99.0, 98.0],
                "ret_60s_bps": [20.0, -30.0, -10.0],
                "flow_60s": [0.60, -0.50, -0.20],
                "basis_change_5m": [1.0, -2.0, -1.0],
                "notional_60s": [2_000.0, 3_000.0, 1_000.0],
            }
        )

    def test_opposite_flow_return_basis_and_open_reclaim_confirm_failure(self) -> None:
        passed, details = candidate.expansion_failure(
            self.frame(),
            event_index=0,
            index=1,
            leader_side=1,
            event_open=100.0,
        )
        self.assertTrue(passed)
        self.assertEqual(details["follower_failure_delay_bars"], 1)
        self.assertGreater(details["follower_failure_reclaim_distance"], 0.0)

    def test_counter_return_without_event_open_reclaim_is_not_failure(self) -> None:
        frame = self.frame()
        frame.loc[1, "close"] = 100.5
        passed, _ = candidate.expansion_failure(
            frame,
            event_index=0,
            index=1,
            leader_side=1,
            event_open=100.0,
        )
        self.assertFalse(passed)

    def test_counter_price_without_counter_flow_is_not_failure(self) -> None:
        frame = self.frame()
        frame.loc[1, "flow_60s"] = 0.10
        passed, _ = candidate.expansion_failure(
            frame,
            event_index=0,
            index=1,
            leader_side=1,
            event_open=100.0,
        )
        self.assertFalse(passed)


if __name__ == "__main__":
    unittest.main()
