from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

import two_stage_auction_resolution_compiler as candidate


class TwoStageAuctionResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.impact = SimpleNamespace(stop_buffer_atr=0.10)
        self.index = pd.date_range("2025-01-01", periods=6, freq="1min", tz="UTC")

    def frame(self, rows: list[dict]) -> pd.DataFrame:
        defaults = {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "atr": 1.0,
            "flow_60s": 0.0,
            "ret_60s_bps": 0.0,
            "basis_change_5m": 0.0,
            "metric_sum_open_interest": 100.0,
        }
        result = [{**defaults, **row} for row in rows]
        while len(result) < len(self.index):
            result.append(dict(defaults))
        return pd.DataFrame(result, index=self.index)

    def intent(
        self,
        scenario: str,
        side: int,
        details: dict,
        *,
        signal_index: int = 1,
        stop: float = 98.0,
    ):
        return candidate.Intent(
            scenario=scenario,
            side=side,
            signal_index=signal_index,
            entry_index=signal_index + 1,
            stop_level=stop,
            event_indices=(0, signal_index),
            details=details,
        )

    def test_alignment_requires_flow_return_and_basis(self) -> None:
        row = pd.Series(
            {"flow_60s": 0.5, "ret_60s_bps": 2.0, "basis_change_5m": 0.2}
        )
        self.assertIsNotNone(candidate.aligned_state(row, 1))
        row["basis_change_5m"] = -0.2
        self.assertIsNone(candidate.aligned_state(row, 1))

    def test_external_parent_relation_separates_failed_discovery(self) -> None:
        self.assertTrue(
            candidate.external_reversal_allowed(
                {
                    "shock_side": -1,
                    "pre_shock_parent_480m_return_bps": 30.0,
                    "impact_absolute_return_bps": 10.0,
                }
            )
        )
        self.assertTrue(
            candidate.external_reversal_allowed(
                {
                    "shock_side": -1,
                    "pre_shock_parent_480m_return_bps": -5.0,
                    "impact_absolute_return_bps": 10.0,
                }
            )
        )
        self.assertFalse(
            candidate.external_reversal_allowed(
                {
                    "shock_side": -1,
                    "pre_shock_parent_480m_return_bps": -30.0,
                    "impact_absolute_return_bps": 10.0,
                }
            )
        )

    def test_balanced_reclaim_failure_routes_persistent_inventory(self) -> None:
        data = self.frame(
            [
                {"metric_sum_open_interest": 100.0},
                {
                    "close": 99.0,
                    "high": 100.2,
                    "metric_sum_open_interest": 110.0,
                },
                {
                    "close": 101.0,
                    "high": 101.2,
                    "low": 98.8,
                    "flow_60s": 0.5,
                    "ret_60s_bps": 2.0,
                    "basis_change_5m": 0.4,
                    "metric_sum_open_interest": 110.0,
                },
            ]
        )
        parent = self.intent(
            candidate.BALANCED_PARENT,
            -1,
            {"boundary_level": 100.0, "attack_index": 0},
            stop=101.5,
        )
        resolved, outcome = candidate.resolve_parent_intent(
            data,
            parent,
            self.index[-1],
            self.impact,
        )
        self.assertEqual(outcome, "failure_route")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.side, 1)
        self.assertEqual(
            resolved.scenario,
            candidate.FAILURE_SCENARIOS[candidate.BALANCED_PARENT],
        )
        self.assertTrue(resolved.details["breakout_inventory_persists"])

    def test_stress_acceptance_failure_routes_deleveraging(self) -> None:
        data = self.frame(
            [
                {"metric_sum_open_interest": 105.0},
                {"close": 101.0, "metric_sum_open_interest": 103.0},
                {
                    "close": 99.0,
                    "high": 101.2,
                    "low": 98.8,
                    "flow_60s": -0.6,
                    "ret_60s_bps": -3.0,
                    "basis_change_5m": -0.5,
                    "metric_sum_open_interest": 100.0,
                },
            ]
        )
        parent = self.intent(
            candidate.STRESS_PARENT,
            1,
            {"sweep_extreme": 100.0, "parent_reversal_signal_index": 0},
            stop=98.0,
        )
        resolved, outcome = candidate.resolve_parent_intent(
            data,
            parent,
            self.index[-1],
            self.impact,
        )
        self.assertEqual(outcome, "failure_route")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.side, -1)
        self.assertTrue(resolved.details["deleveraging_not_new_inventory"])

    def test_balanced_original_side_requires_inventory_to_stop_expanding(self) -> None:
        data = self.frame(
            [
                {"metric_sum_open_interest": 100.0},
                {"close": 99.0, "metric_sum_open_interest": 110.0},
                {
                    "close": 98.5,
                    "high": 100.1,
                    "low": 98.0,
                    "flow_60s": -0.5,
                    "ret_60s_bps": -2.0,
                    "basis_change_5m": -0.4,
                    "metric_sum_open_interest": 110.0,
                },
            ]
        )
        parent = self.intent(
            candidate.BALANCED_PARENT,
            -1,
            {"boundary_level": 100.0, "attack_index": 0},
            stop=101.5,
        )
        resolved, outcome = candidate.resolve_parent_intent(
            data,
            parent,
            self.index[-1],
            self.impact,
        )
        self.assertEqual(outcome, "original_hold")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.side, -1)
        self.assertTrue(
            resolved.details["breakout_inventory_no_longer_expanding"]
        )


if __name__ == "__main__":
    unittest.main()
