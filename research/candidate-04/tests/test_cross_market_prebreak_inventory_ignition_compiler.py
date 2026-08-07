from __future__ import annotations

import unittest

import pandas as pd

import cross_market_prebreak_inventory_ignition_compiler as candidate


class LeaderFirstUnderreactionTests(unittest.TestCase):
    def frame(self, prior_return: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "high": [104.0, 104.0, 104.0, 104.0, 104.0, 103.0],
                "low": [100.0, 100.0, 100.0, 100.0, 100.0, 100.5],
                "close": [102.0, 102.0, 102.0, 102.0, 102.0, 101.0],
                "ret_60s_bps": [0.0, 0.0, 0.0, 0.0, prior_return, -1.0],
            }
        )

    def test_preceding_same_direction_tail_invalidates_leader_first_state(self) -> None:
        passed, _ = candidate.leader_first_underreacted(
            self.frame(-10.0),
            5,
            -1,
            5.0,
        )
        self.assertFalse(passed)

    def test_small_preceding_response_preserves_underreaction(self) -> None:
        passed, boundary = candidate.leader_first_underreacted(
            self.frame(-2.0),
            5,
            -1,
            5.0,
        )
        self.assertTrue(passed)
        self.assertEqual(boundary, 100.0)


class InventoryIgnitionTests(unittest.TestCase):
    def frame(self, *, signal_high: float = 104.0) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "close": [100.0, 100.0, 100.0, 100.0, 101.0, 102.0, 103.0],
                "high": [101.0, 101.0, 101.0, 101.0, 102.0, 103.0, signal_high],
                "low": [99.0, 99.0, 99.0, 99.0, 100.0, 101.0, 102.0],
                "flow_60s": [0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 0.7],
                "ret_60s_bps": [0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0],
                "basis_change_5m": [0.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.4],
                "notional_60s": [1.0] * 7,
                "metric_sum_open_interest": [100.0, 100.0, 100.0, 100.0, 100.0, 100.2, 101.0],
            }
        )

    def test_flow_basis_and_oi_can_confirm_before_target_is_touched(self) -> None:
        passed, details = candidate.follower_inventory_ignition(
            self.frame(),
            leader_index=5,
            signal_index=6,
            side=1,
            structure=105.0,
            flow_cutoff=0.5,
            oi_cutoff=0.005,
        )
        self.assertTrue(passed)
        self.assertTrue(details["follower_target_untouched"])
        self.assertEqual(details["causal_target_reference"], 105.0)
        self.assertEqual(details["causal_target_observed_index"], 4)

    def test_intrabar_touch_invalidates_prebreak_ignition(self) -> None:
        passed, _ = candidate.follower_inventory_ignition(
            self.frame(signal_high=105.0),
            leader_index=5,
            signal_index=6,
            side=1,
            structure=105.0,
            flow_cutoff=0.5,
            oi_cutoff=0.005,
        )
        self.assertFalse(passed)


class BoundaryGeometryTests(unittest.TestCase):
    def test_only_cost_aware_boundary_targets_are_emitted(self) -> None:
        frame = pd.DataFrame({"close": [100.0, 102.0]})
        good = candidate.base.Candidate(
            symbol="ETHUSDT",
            leader_index=0,
            signal_index=1,
            side=1,
            stop_level=100.0,
            confirmation_notional=1.0,
            details={"causal_target_reference": 105.0},
        )
        bad = candidate.base.Candidate(
            symbol="ETHUSDT",
            leader_index=0,
            signal_index=1,
            side=1,
            stop_level=100.0,
            confirmation_notional=1.0,
            details={"causal_target_reference": 103.0},
        )
        selected, rejected = candidate.tradeable_boundary_candidates(
            [good, bad],
            {"ETHUSDT": frame},
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(rejected, 1)
        self.assertGreaterEqual(
            selected[0].details["causal_target_net_r_at_compilation"],
            candidate.MINIMUM_NET_R,
        )


if __name__ == "__main__":
    unittest.main()
