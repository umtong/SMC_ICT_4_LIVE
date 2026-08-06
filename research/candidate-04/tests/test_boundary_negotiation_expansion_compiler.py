from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

import boundary_negotiation_expansion_compiler as candidate


class BoundaryNegotiationExpansionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = pd.date_range("2025-01-01", periods=8, freq="1min", tz="UTC")
        self.config = SimpleNamespace(
            stress_failure_wait_minutes=6,
            stress_inventory_quantile=0.95,
            stress_inventory_quantile_window_minutes=4,
            stress_inventory_quantile_min_periods=4,
        )
        self.impact = SimpleNamespace(stop_buffer_atr=0.10)

    def frame(self, rows: list[dict]) -> pd.DataFrame:
        defaults = {
            "open": 100.0,
            "high": 100.5,
            "low": 99.5,
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

    def stress_parent(self):
        return candidate.Intent(
            scenario=candidate.v30.STRESS_PARENT,
            side=1,
            signal_index=1,
            entry_index=2,
            stop_level=98.0,
            event_indices=(0, 1),
            details={
                "sweep_extreme": 100.0,
                "parent_reversal_signal_index": 0,
            },
        )

    def test_cutoff_is_shifted_before_current_observation(self) -> None:
        data = pd.DataFrame(
            {"ret_60s_bps": [1.0, 2.0, 3.0, 4.0, 100.0]},
        )
        cutoff = candidate.past_only_displacement_cutoff(data, self.config)
        self.assertTrue(pd.isna(cutoff.iloc[3]))
        self.assertAlmostEqual(float(cutoff.iloc[4]), 3.85)

    def test_two_sided_negotiation_routes_settled_short_expansion(self) -> None:
        data = self.frame(
            [
                {"metric_sum_open_interest": 105.0},
                {"close": 101.0, "high": 101.5, "metric_sum_open_interest": 104.0},
                {"close": 99.0, "low": 98.8, "metric_sum_open_interest": 103.0},
                {"close": 101.2, "high": 101.7, "metric_sum_open_interest": 102.0},
                {
                    "close": 98.0,
                    "high": 100.2,
                    "low": 97.7,
                    "flow_60s": -0.7,
                    "ret_60s_bps": -12.0,
                    "basis_change_5m": -0.8,
                    "metric_sum_open_interest": 100.0,
                },
            ]
        )
        cutoff = pd.Series(1.0, index=data.index)
        resolved, outcome = candidate.resolve_negotiation(
            data,
            self.stress_parent(),
            self.index[-1],
            self.config,
            self.impact,
            cutoff,
        )
        self.assertEqual(outcome, "settled_expansion")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.side, -1)
        self.assertEqual(
            resolved.scenario,
            "STRESS_SETTLED_DELEVERAGING_REVERSAL",
        )
        self.assertGreaterEqual(resolved.details["boundary_transitions"], 2)
        self.assertGreater(resolved.stop_level, data["close"].iloc[4])
        self.assertEqual(resolved.details["prior_close_range_low"], 99.0)
        self.assertEqual(resolved.details["prior_close_range_high"], 101.2)

    def test_single_cross_never_becomes_settled_negotiation(self) -> None:
        data = self.frame(
            [
                {"metric_sum_open_interest": 105.0},
                {"close": 101.0, "metric_sum_open_interest": 104.0},
                {"close": 99.0, "metric_sum_open_interest": 103.0},
                {
                    "close": 98.0,
                    "flow_60s": -0.8,
                    "ret_60s_bps": -15.0,
                    "basis_change_5m": -1.0,
                    "metric_sum_open_interest": 100.0,
                },
            ]
        )
        cutoff = pd.Series(1.0, index=data.index)
        resolved, outcome = candidate.resolve_negotiation(
            data,
            self.stress_parent(),
            self.index[4],
            self.config,
            self.impact,
            cutoff,
        )
        self.assertIsNone(resolved)
        self.assertEqual(outcome, "unresolved")

    def test_expansion_must_leave_prior_close_range(self) -> None:
        data = self.frame(
            [
                {"metric_sum_open_interest": 105.0},
                {"close": 101.0, "metric_sum_open_interest": 104.0},
                {"close": 99.0, "metric_sum_open_interest": 103.0},
                {"close": 101.2, "metric_sum_open_interest": 102.0},
                {
                    "close": 99.5,
                    "flow_60s": -0.7,
                    "ret_60s_bps": -12.0,
                    "basis_change_5m": -0.8,
                    "metric_sum_open_interest": 100.0,
                },
            ]
        )
        cutoff = pd.Series(1.0, index=data.index)
        resolved, outcome = candidate.resolve_negotiation(
            data,
            self.stress_parent(),
            self.index[4],
            self.config,
            self.impact,
            cutoff,
        )
        self.assertIsNone(resolved)
        self.assertEqual(outcome, "unresolved")


if __name__ == "__main__":
    unittest.main()
