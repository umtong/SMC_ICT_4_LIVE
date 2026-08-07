from __future__ import annotations

import inspect
import unittest

import relative_value_context as context
import strategy_v47_relative_value as v47


class V47ContractTest(unittest.TestCase):
    def tearDown(self) -> None:
        context.reset()

    def test_peer_context_is_strictly_prior(self) -> None:
        context.publish(symbol='ETHUSDT', ts=100, close=100.0, atr=1.0)
        context.publish(symbol='ETHUSDT', ts=200, close=101.0, atr=1.0)
        values=context.completed_history('ETHUSDT', before_ts=200, count=5)
        self.assertEqual([item.ts for item in values],[100])

    def test_robust_threshold_is_fixed_three_mad(self) -> None:
        self.assertEqual(v47.ROBUST_Z,3.0)
        self.assertEqual(v47.MIN_RESIDUAL_OBSERVATIONS,60)

    def test_local_entry_still_uses_inherited_pending_path(self) -> None:
        source=inspect.getsource(v47.RelativeValueDislocationStrategy._maybe_arm_relative_value)
        self.assertIn('PendingSetup',source)
        self.assertIn('rejection_confirmation_bars',source)
        self.assertIn('self.pending',source)

    def test_no_same_timestamp_or_risk_override(self) -> None:
        source=inspect.getsource(v47)
        self.assertIn('before_ts=ts',source)
        for token in ('risk_fraction =','max_notional','leverage_cap','match_order','calculate_pnl'):
            self.assertNotIn(token,source)


if __name__=='__main__':
    unittest.main()
