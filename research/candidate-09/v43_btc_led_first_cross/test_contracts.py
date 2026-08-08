import inspect
import unittest

import portfolio_strategy as strategy
import run_portfolio


class Candidate43Contracts(unittest.TestCase):
    def test_btc_remains_v35_and_alts_use_new_family(self):
        self.assertFalse(
            issubclass(
                strategy.SharedAccountV43BTCStrategy,
                strategy.BtcLedFirstCrossMixin,
            )
        )
        for cls in (
            strategy.SharedAccountV43ETHStrategy,
            strategy.SharedAccountV43SOLStrategy,
            strategy.SharedAccountV43XRPStrategy,
        ):
            self.assertTrue(issubclass(cls, strategy.BtcLedFirstCrossMixin))

    def test_strictly_prior_leader_precedes_local_entry(self):
        leader = inspect.getsource(
            strategy.BtcLedFirstCrossMixin._candidate43_leader_decision
        )
        detect = inspect.getsource(strategy.BtcLedFirstCrossMixin._detect_sweep)
        self.assertIn('latest_before("BTCUSDT", current_ts)', leader)
        self.assertIn("age_ns <= maximum_age_ns", leader)
        self.assertIn("directional_return >= local_progress_atr", leader)
        local = detect.index("_candidate43_local_impulse")
        context = detect.index("_candidate43_leader_decision")
        entry = detect.index("_submit_entry")
        self.assertLess(local, context)
        self.assertLess(context, entry)

    def test_entry_invalidation_and_target_stay_in_new_leg(self):
        detect = inspect.getsource(strategy.BtcLedFirstCrossMixin._detect_sweep)
        impulse = inspect.getsource(
            strategy.BtcLedFirstCrossMixin._candidate43_local_impulse
        )
        self.assertIn('branch="ACCEPTANCE"', detect)
        self.assertIn("pool_level=selected.level", detect)
        self.assertIn('"BTC_LED_FIRST_CROSS_CONFIRMED"', detect)
        self.assertIn("stack_crossed", impulse)
        self.assertNotIn("FIRST_RETEST_ARMED", detect)

    def test_exact_control_changes_only_btc_context(self):
        configured = inspect.getsource(run_portfolio.prepare_configs)
        self.assertIn('"candidate43_require_btc_leader"', configured)
        self.assertIn('"candidate43_leader_max_age_bars": 3', configured)
        self.assertEqual(
            set(run_portfolio.VARIANTS),
            {"btc-led-first-cross", "local-first-cross-control"},
        )

    def test_global_slot_wraps_actual_submit_boundary(self):
        source = inspect.getsource(strategy.SharedSlotMixin)
        self.assertIn("def _submit_entry", source)
        self.assertIn("acquire_entry_intent", source)
        self.assertIn("position_opened", source)
        self.assertIn("position_closed", source)


if __name__ == "__main__":
    unittest.main()
