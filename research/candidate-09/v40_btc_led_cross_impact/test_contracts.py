import inspect
import unittest

import features
import portfolio_strategy as strategy


class Candidate40Contracts(unittest.TestCase):
    def test_frozen_instrument_ticks_drive_footprint(self):
        self.assertEqual(features._TICK_SIZES["BTCUSDT"], 0.1)
        self.assertEqual(features._TICK_SIZES["ETHUSDT"], 0.01)
        self.assertEqual(features._TICK_SIZES["SOLUSDT"], 0.01)
        self.assertEqual(features._TICK_SIZES["XRPUSDT"], 0.0001)

    def test_leader_lookup_is_strictly_prior(self):
        context = strategy.CompletedLeaderContext()
        state = strategy.CompletedLeaderState(
            symbol="BTCUSDT",
            ts_event=100,
            return_atr=1.0,
            flow_60s=1.0,
            efficiency_60s=1.0,
            footprint_delta_60s=1.0,
            stacked_buy_levels=3,
            stacked_sell_levels=0,
        )
        context.publish(state)
        self.assertIsNone(context.latest_before("BTCUSDT", 100))
        self.assertEqual(context.latest_before("BTCUSDT", 101), state)

    def test_cross_impact_roles_are_separated(self):
        complete = inspect.getsource(strategy.LaggedBtcContextMixin._complete_parent)
        decision = inspect.getsource(
            strategy.LaggedBtcContextMixin._candidate40_leader_decision
        )
        self.assertIn("interaction_ts_event", complete)
        self.assertIn("AuctionDecision.ACCEPTANCE_CONTINUATION", complete)
        self.assertIn("directional_return >= max(0.0, target_progress_atr)", decision)
        self.assertIn("leader.stacked_buy_levels", decision)
        self.assertIn("ALT_TRUE_ACCEPTANCE_WITHOUT_PRIOR_BTC_LEADERSHIP", complete)

    def test_global_slot_wraps_actual_submit_entry(self):
        source = inspect.getsource(strategy.SharedSlotMixin)
        self.assertIn("def _submit_entry", source)
        self.assertNotIn("def _submit_market_bracket", source)
        self.assertIn("acquire_entry_intent", source)
        self.assertIn("position_opened", source)
        self.assertIn("position_closed", source)


if __name__ == "__main__":
    unittest.main()
