import inspect
import unittest

import run_experiment
import strategy


class Candidate50Contracts(unittest.TestCase):
    def test_no_inherited_liquidity_sweep_entry(self):
        source = inspect.getsource(strategy.Candidate16Strategy._detect_sweep)
        self.assertIn("Completed-auction pools remain context only", source)
        self.assertNotIn("super()._detect_sweep", source)

    def test_full_metrics_delay_is_fixed_and_causal(self):
        config_source = inspect.getsource(strategy.Candidate16Config)
        arm_source = inspect.getsource(strategy.Candidate16Strategy._candidate50_maybe_arm)
        self.assertIn("candidate50_event_minutes: int = EVENT_MINUTES", config_source)
        self.assertIn(
            "candidate50_publication_delay_minutes: int = PUBLICATION_DELAY_MINUTES",
            config_source,
        )
        self.assertIn("event_end_ns = metrics_observed_ns - delay_ns", arm_source)
        self.assertIn("metrics_observed_ns > ts_event", arm_source)

    def test_forced_deleveraging_has_distinct_oi_and_premium_roles(self):
        source = inspect.getsource(strategy.Candidate16Strategy._candidate50_maybe_arm)
        self.assertIn("oi_change < 0.0", source)
        self.assertIn("direction * premium_change > 0.0", source)
        self.assertIn("candidate50_require_forced_deleveraging", source)

    def test_exhaustion_is_effort_without_extension(self):
        source = inspect.getsource(strategy.Candidate16Strategy._candidate50_maybe_arm)
        self.assertIn("delay_flow_bars", source)
        self.assertIn("extension_atr > self.config.router_failed_max_progress_atr", source)
        self.assertIn("candidate50_delay_effort_without_result", source)

    def test_entry_waits_for_new_opposite_leg_and_value_migration(self):
        source = inspect.getsource(strategy.Candidate16Strategy._candidate50_process_watch)
        self.assertIn("confirmation_passes", source)
        self.assertIn("side * (poc - watch.event_poc) > 0.0", source)
        self.assertIn("side * delta > 0.0", source)
        self.assertIn("self._candidate50_submit", source)

    def test_entry_stop_and_target_share_the_shock_leg(self):
        source = inspect.getsource(strategy.Candidate16Strategy._candidate50_submit)
        self.assertIn("stop = watch.shock_extreme", source)
        self.assertIn("target = watch.boundary", source)
        self.assertIn("target_r < self.config.min_target_net_r", source)
        self.assertIn("risk_budget = equity * self.config.risk_fraction", source)

    def test_exact_control_changes_only_forced_deleveraging_requirement(self):
        self.assertEqual(
            run_experiment.VARIANTS,
            {
                "forced-deleveraging-exhaustion": True,
                "price-flow-exhaustion-control": False,
            },
        )
        source = inspect.getsource(run_experiment.configured)
        self.assertIn("candidate50_require_forced_deleveraging", source)
        self.assertIn("candidate50_event_minutes", source)
        self.assertIn("candidate50_publication_delay_minutes", source)


if __name__ == "__main__":
    unittest.main()
