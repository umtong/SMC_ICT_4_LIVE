from __future__ import annotations

import unittest

from session_expansion_router import ExpansionDecision
from session_expansion_router import ExpansionRetest
from session_expansion_router import RetestObservation
from session_expansion_router import advance_expansion_retest
from session_expansion_router import expansion_breakout_side


class ExpansionBreakoutTests(unittest.TestCase):
    def _breakout(self, **overrides):
        values = {
            "opening_high": 100.0,
            "opening_low": 90.0,
            "high": 103.0,
            "low": 99.0,
            "close": 102.0,
            "atr": 5.0,
            "flow_60s": 0.20,
            "ret_60s_bps": 12.0,
            "efficiency_60s": 0.50,
            "notional_burst": 1.25,
            "oi_expanded": True,
            "min_progress_atr": 0.24,
            "min_efficiency": 0.34,
            "min_close_location": 0.56,
        }
        values.update(overrides)
        return expansion_breakout_side(**values)

    def test_long_breakout_requires_delivered_price_flow_and_fresh_oi(self):
        self.assertEqual(self._breakout(), 1)
        self.assertEqual(self._breakout(oi_expanded=False), 0)
        self.assertEqual(self._breakout(flow_60s=-0.20), 0)
        self.assertEqual(self._breakout(notional_burst=1.0), 0)
        self.assertEqual(self._breakout(efficiency_60s=0.20), 0)

    def test_short_breakout_is_symmetric(self):
        self.assertEqual(
            self._breakout(
                high=91.0,
                low=87.0,
                close=88.0,
                flow_60s=-0.20,
                ret_60s_bps=-12.0,
            ),
            -1,
        )


class FirstRetestTests(unittest.TestCase):
    def _state(self, **overrides):
        values = {
            "scenario_id": "sxe-1",
            "session_key": 2026010100,
            "side": 1,
            "boundary": 100.0,
            "opposite_boundary": 90.0,
            "breakout_index": 10,
            "last_index": 10,
            "expires_index": 14,
            "breakout_extreme": 103.0,
            "max_counterflow": 0.08,
            "min_close_location": 0.56,
        }
        values.update(overrides)
        return ExpansionRetest(**values)

    def test_first_touch_confirms_only_with_independent_book_defense(self):
        result = advance_expansion_retest(
            self._state(),
            RetestObservation(
                bar_index=11,
                high=103.0,
                low=99.5,
                close=102.0,
                flow_15s=0.01,
                depth_imbalance_1=0.10,
                liquidity_ahead_change_1m=-0.05,
            ),
        )
        self.assertEqual(result.decision, ExpansionDecision.CONFIRMED)

    def test_first_weak_touch_closes_instead_of_waiting_for_second_retest(self):
        result = advance_expansion_retest(
            self._state(),
            RetestObservation(
                bar_index=11,
                high=103.0,
                low=99.5,
                close=102.0,
                flow_15s=0.01,
                depth_imbalance_1=-0.10,
                liquidity_ahead_change_1m=0.05,
            ),
        )
        self.assertEqual(result.decision, ExpansionDecision.INVALIDATED)
        self.assertIn("FIRST_RETEST", result.reason)

    def test_body_reentry_invalidates_before_entry(self):
        result = advance_expansion_retest(
            self._state(),
            RetestObservation(
                bar_index=11,
                high=102.0,
                low=98.0,
                close=99.0,
                flow_15s=0.20,
                depth_imbalance_1=0.20,
                liquidity_ahead_change_1m=-0.20,
            ),
        )
        self.assertEqual(result.decision, ExpansionDecision.INVALIDATED)
        self.assertIn("BODY_FAILED", result.reason)

    def test_no_touch_waits_then_expires(self):
        waiting = advance_expansion_retest(
            self._state(),
            RetestObservation(
                bar_index=11,
                high=104.0,
                low=101.0,
                close=103.0,
                flow_15s=0.10,
                depth_imbalance_1=0.10,
                liquidity_ahead_change_1m=-0.10,
            ),
        )
        self.assertEqual(waiting.decision, ExpansionDecision.WAITING)
        expired = advance_expansion_retest(
            waiting,
            RetestObservation(
                bar_index=14,
                high=105.0,
                low=101.0,
                close=104.0,
                flow_15s=0.10,
                depth_imbalance_1=0.10,
                liquidity_ahead_change_1m=-0.10,
            ),
        )
        self.assertEqual(expired.decision, ExpansionDecision.EXPIRED)


if __name__ == "__main__":
    unittest.main()
