import inspect
import unittest

import pandas as pd

import positioning_contract
import strategy


class Candidate41Contracts(unittest.TestCase):
    def test_archive_owner_resolves_conflicting_midnight_row(self):
        observed = pd.Timestamp("2024-05-01 00:05:00", tz="UTC")
        frame = pd.DataFrame(
            {
                "metrics_observed_time": [observed, observed],
                "_source_archive_day": ["2024-04-30", "2024-05-01"],
                "sum_open_interest": [1.0, 2.0],
            }
        )
        result = positioning_contract._canonicalize_archive_boundaries(frame)
        self.assertEqual(len(result), 1)
        self.assertEqual(float(result.iloc[0]["sum_open_interest"]), 2.0)
        self.assertNotIn("_source_archive_day", result.columns)

    def test_full_metrics_delay_precedes_entry_arm(self):
        source = inspect.getsource(strategy.Candidate16Strategy._candidate41_maybe_arm_reset)
        metrics = source.index("metrics_observed_ns")
        event_end = source.index("event_end_ns = metrics_observed_ns - delay_ns")
        residence = source.index("residence_pass = all")
        pending = source.index("self.pending = PendingSetup")
        self.assertLess(metrics, event_end)
        self.assertLess(event_end, residence)
        self.assertLess(residence, pending)

    def test_context_state_and_execution_roles_are_distinct(self):
        source = inspect.getsource(strategy.Candidate16Strategy)
        self.assertIn("oi_change < 0.0", source)
        self.assertIn("direction * premium_change <= 0.0", source)
        self.assertIn("candidate41_delayed_residence", source)
        self.assertIn('branch="ACCEPTANCE"', source)
        self.assertIn("FIRST_RETEST_ARMED", source)
        self.assertIn("def _detect_sweep", source)

    def test_single_ablation_only_removes_leverage_reset(self):
        config = inspect.getsource(strategy.Candidate16Config)
        decision = inspect.getsource(strategy.Candidate16Strategy._candidate41_maybe_arm_reset)
        self.assertIn("candidate41_require_leverage_reset: bool = True", config)
        self.assertIn(
            "self.config.candidate41_require_leverage_reset and not leverage_reset",
            decision,
        )
        self.assertIn("candidate41_price_only_control_paths", decision)


if __name__ == "__main__":
    unittest.main()
