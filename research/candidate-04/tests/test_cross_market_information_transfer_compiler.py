from __future__ import annotations

import unittest

import pandas as pd

import cross_market_information_transfer_compiler as candidate


class CrossMarketInformationTransferTests(unittest.TestCase):
    def test_leader_event_requires_return_flow_basis_and_oi_creation(self) -> None:
        row = pd.Series(
            {
                "ret_60s_bps": 12.0,
                "flow_60s": 0.40,
                "eff_60s": 0.80,
                "basis_change_5m": 1.0,
                "metric_oi_change_15m": 0.01,
            }
        )
        passed, side = candidate.leader_information_event(
            row,
            return_cutoff=10.0,
            flow_cutoff=0.30,
            efficiency_cutoff=0.70,
            oi_cutoff=0.005,
        )
        self.assertTrue(passed)
        self.assertEqual(side, 1)
        row["metric_oi_change_15m"] = -0.01
        self.assertFalse(
            candidate.leader_information_event(
                row,
                return_cutoff=10.0,
                flow_cutoff=0.30,
                efficiency_cutoff=0.70,
                oi_cutoff=0.005,
            )[0]
        )

    def follower_frame(self) -> pd.DataFrame:
        rows = 12
        frame = pd.DataFrame(
            {
                "high": [100.0] * rows,
                "low": [99.0] * rows,
                "close": [99.5] * rows,
                "ret_60s_bps": [0.0] * rows,
                "flow_60s": [0.0] * rows,
                "basis_change_5m": [0.0] * rows,
                "notional_60s": [1000.0] * rows,
                "metric_sum_open_interest": [1000.0] * rows,
                "atr": [1.0] * rows,
            }
        )
        return frame

    def test_follower_must_be_unbroken_and_underreacted_at_leader_event(self) -> None:
        frame = self.follower_frame()
        passed, boundary = candidate.follower_underreacted(
            frame,
            index=5,
            side=1,
            median_absolute_return=2.0,
        )
        self.assertTrue(passed)
        self.assertEqual(boundary, 100.0)
        frame.loc[5, "close"] = 101.0
        self.assertFalse(
            candidate.follower_underreacted(
                frame,
                index=5,
                side=1,
                median_absolute_return=2.0,
            )[0]
        )

    def test_confirmation_requires_structure_flow_basis_and_state_oi(self) -> None:
        frame = self.follower_frame()
        frame.loc[5, "metric_sum_open_interest"] = 1000.0
        frame.loc[7, ["close", "flow_60s", "ret_60s_bps", "basis_change_5m", "notional_60s", "metric_sum_open_interest"]] = [
            101.0,
            0.5,
            4.0,
            1.0,
            3000.0,
            1010.0,
        ]
        passed, details = candidate.follower_confirmation(
            frame,
            leader_index=6,
            signal_index=7,
            side=1,
            structure=100.0,
            flow_cutoff=0.3,
            oi_cutoff=0.005,
        )
        self.assertTrue(passed)
        self.assertGreater(details["follower_state_open_interest_change"], 0.005)
        frame.loc[7, "basis_change_5m"] = -1.0
        self.assertFalse(
            candidate.follower_confirmation(
                frame,
                leader_index=6,
                signal_index=7,
                side=1,
                structure=100.0,
                flow_cutoff=0.3,
                oi_cutoff=0.005,
            )[0]
        )

    def test_candidate_selection_prefers_earliest_then_liquid(self) -> None:
        details = {}
        values = [
            candidate.Candidate("SOLUSDT", 10, 13, 1, 99.0, 5000.0, details),
            candidate.Candidate("ETHUSDT", 10, 12, 1, 99.0, 1000.0, details),
            candidate.Candidate("XRPUSDT", 10, 12, 1, 99.0, 2000.0, details),
        ]
        selected = candidate.select_candidate(values)
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.symbol, "XRPUSDT")


if __name__ == "__main__":
    unittest.main()
