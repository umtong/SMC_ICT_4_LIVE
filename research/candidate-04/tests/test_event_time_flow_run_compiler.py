from __future__ import annotations

import unittest

import pandas as pd

import event_time_flow_run_compiler as candidate


class ThresholdTests(unittest.TestCase):
    def test_shifted_cutoff_excludes_current_observation(self) -> None:
        values = pd.Series([1.0, 2.0, 3.0, 999.0])
        result = candidate.shifted_quantile(values, 0.5, window=3, minimum=3)
        self.assertEqual(float(result.iloc[3]), 2.0)


class FlowRunTests(unittest.TestCase):
    def _thresholds(self, rows: int) -> candidate.RunThresholds:
        series = lambda value: pd.Series([value] * rows)
        return candidate.RunThresholds(
            median_abs_flow=series(0.2),
            tail_cumulative_effort=series(100.0),
            directional_return_q60=series(1.0),
            efficiency_q30=series(0.2),
            efficiency_q70=series(0.6),
            positive_oi_step_median=series(0.005),
        )

    def test_sign_change_closes_prior_run_without_lookahead(self) -> None:
        data = pd.DataFrame(
            {
                "open": [100.0, 101.0, 102.0, 101.0, 100.0],
                "high": [101.5, 102.5, 103.0, 101.5, 100.5],
                "low": [99.5, 100.5, 101.5, 99.5, 98.5],
                "close": [101.0, 102.0, 102.5, 100.0, 99.0],
                "flow_60s": [0.5, 0.6, 0.4, -0.7, -0.8],
                "notional_60s": [1000.0] * 5,
                "ret_60s_bps": [10.0, 9.0, 4.0, -20.0, -10.0],
                "metric_sum_open_interest": [100.0, 101.0, 102.0, 101.0, 100.0],
            }
        )
        runs = candidate.build_flow_runs(data, self._thresholds(len(data)))
        self.assertEqual(len(runs), 2)
        self.assertEqual((runs[0].start_index, runs[0].end_index), (0, 2))
        self.assertEqual(runs[0].side, 1)
        self.assertEqual((runs[1].start_index, runs[1].end_index), (3, 4))
        self.assertEqual(runs[1].side, -1)

    def test_informed_run_requires_material_oi_and_high_efficiency(self) -> None:
        data = pd.DataFrame(
            {
                "basis_change_5m": [2.0],
            }
        )
        thresholds = self._thresholds(1)
        accepted = candidate.FlowRun(
            start_index=0,
            end_index=0,
            side=1,
            bars=3,
            cumulative_effort=200.0,
            directional_return_bps=4.0,
            path_bps=5.0,
            efficiency=0.8,
            high=101.0,
            low=99.0,
            start_open_interest=100.0,
            end_open_interest=101.0,
            open_interest_change=0.01,
        )
        self.assertTrue(candidate.run_is_informed(accepted, thresholds, data))
        rejected = candidate.FlowRun(
            **{
                field: getattr(accepted, field)
                for field in accepted.__dataclass_fields__
                if field != "open_interest_change"
            },
            open_interest_change=0.001,
        )
        self.assertFalse(candidate.run_is_informed(rejected, thresholds, data))

    def test_low_impact_attack_requires_oi_nonexpansion(self) -> None:
        thresholds = self._thresholds(1)
        contracted = candidate.FlowRun(
            start_index=0,
            end_index=0,
            side=-1,
            bars=3,
            cumulative_effort=200.0,
            directional_return_bps=0.5,
            path_bps=10.0,
            efficiency=0.05,
            high=101.0,
            low=99.0,
            start_open_interest=100.0,
            end_open_interest=99.0,
            open_interest_change=-0.01,
        )
        self.assertTrue(candidate.run_is_low_impact_attack(contracted, thresholds))
        expanded = candidate.FlowRun(
            **{
                field: getattr(contracted, field)
                for field in contracted.__dataclass_fields__
                if field != "open_interest_change"
            },
            open_interest_change=0.01,
        )
        self.assertFalse(candidate.run_is_low_impact_attack(expanded, thresholds))


class PoolContractTests(unittest.TestCase):
    def test_exact_boundary_reclaim_is_directional(self) -> None:
        self.assertTrue(candidate.pool_reclaimed(1, 100.0, 99.9))
        self.assertFalse(candidate.pool_reclaimed(1, 100.0, 100.1))
        self.assertTrue(candidate.pool_reclaimed(-1, 100.0, 100.1))
        self.assertFalse(candidate.pool_reclaimed(-1, 100.0, 99.9))


class PullbackContractTests(unittest.TestCase):
    def test_continuation_requires_weak_counter_effort_and_oi_retention(self) -> None:
        self.assertLess(candidate.MAX_COUNTER_EFFORT, 1.0)
        self.assertGreater(candidate.MIN_OI_RETENTION, 0.99)
        self.assertGreater(candidate.MIN_RETRACE, 0.0)
        self.assertLess(candidate.MAX_RETRACE, 1.0)


if __name__ == "__main__":
    unittest.main()
