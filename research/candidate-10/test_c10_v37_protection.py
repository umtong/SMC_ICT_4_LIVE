from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from logic import LogicConfig

from c10_v37_overlay import first_favorable_internal_pivot
from c10_v37_overlay import internal_pivot_protection_enabled
from c10_v37_state import ConfirmedInternalPivotProtectionEngine


class V37PureProtectionTest(unittest.TestCase):
    def test_exact_short_control_lower_high_moves_stop_directionally(self) -> None:
        decision = first_favorable_internal_pivot(
            direction="SHORT",
            internal_highs=[
                (1_688_046_180_000_000_000, 1_688_046_360_000_000_000, 18.129),
                (1_688_048_580_000_000_000, 1_688_049_060_000_000_000, 17.607),
            ],
            internal_lows=[],
            entry_fill_ts_ns=1_688_047_200_000_000_000,
            observed_ts_ns=1_688_049_060_000_000_000,
            original_stop=17.653288,
            reference_extreme=17.649,
            current_price=17.50,
            target_price=16.956,
            atr=0.0536,
            stop_buffer_atr=0.08,
            tick_size=0.001,
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.pivot_level, 17.607)
        self.assertEqual(decision.protective_stop, 17.612)
        self.assertLess(decision.protective_stop, decision.original_stop)

    def test_long_requires_higher_low_than_ce_retest_extreme(self) -> None:
        decision = first_favorable_internal_pivot(
            direction="LONG",
            internal_highs=[],
            internal_lows=[
                (110, 120, 99.5),
                (130, 140, 101.2),
            ],
            entry_fill_ts_ns=100,
            observed_ts_ns=140,
            original_stop=99.0,
            reference_extreme=100.0,
            current_price=103.0,
            target_price=108.0,
            atr=2.0,
            stop_buffer_atr=0.08,
            tick_size=0.1,
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.pivot_level, 101.2)
        self.assertEqual(decision.protective_stop, 101.0)

    def test_pivot_event_and_confirmation_must_both_follow_real_fill(self) -> None:
        decision = first_favorable_internal_pivot(
            direction="LONG",
            internal_highs=[],
            internal_lows=[(90, 110, 101.0)],
            entry_fill_ts_ns=100,
            observed_ts_ns=110,
            original_stop=99.0,
            reference_extreme=100.0,
            current_price=103.0,
            target_price=108.0,
            atr=2.0,
            stop_buffer_atr=0.08,
            tick_size=0.1,
        )
        self.assertIsNone(decision)

    def test_candidate_must_reduce_risk_and_remain_live(self) -> None:
        widening = first_favorable_internal_pivot(
            direction="LONG",
            internal_highs=[],
            internal_lows=[(110, 120, 101.0)],
            entry_fill_ts_ns=100,
            observed_ts_ns=120,
            original_stop=101.1,
            reference_extreme=100.0,
            current_price=103.0,
            target_price=108.0,
            atr=2.0,
            stop_buffer_atr=0.08,
            tick_size=0.1,
        )
        self.assertIsNone(widening)
        already_lost = first_favorable_internal_pivot(
            direction="LONG",
            internal_highs=[],
            internal_lows=[(110, 120, 101.0)],
            entry_fill_ts_ns=100,
            observed_ts_ns=120,
            original_stop=99.0,
            reference_extreme=100.0,
            current_price=100.7,
            target_price=108.0,
            atr=2.0,
            stop_buffer_atr=0.08,
            tick_size=0.1,
        )
        self.assertIsNone(already_lost)

    def test_environment_ablation_is_exact(self) -> None:
        with patch.dict(os.environ, {"C10_V37_INTERNAL_PIVOT_PROTECTION": "0"}):
            self.assertFalse(internal_pivot_protection_enabled())
        with patch.dict(os.environ, {"C10_V37_INTERNAL_PIVOT_PROTECTION": "1"}):
            self.assertTrue(internal_pivot_protection_enabled())


class V37StateEvidenceTest(unittest.TestCase):
    def test_event_separates_pivot_time_from_confirmation_time(self) -> None:
        engine = ConfirmedInternalPivotProtectionEngine(
            LogicConfig(),
            "TEST-PERP.BINANCE",
        )
        engine.active_trade_id = "SCENARIO-1"
        engine.active_trade_state = "POSITION"
        engine.mark_internal_pivot_protected(
            observed_ts_ns=200,
            pivot_event_ts_ns=150,
            direction="SHORT",
            pivot_level=99.0,
            reference_extreme=101.0,
            protective_stop=99.5,
            original_stop=102.0,
        )
        self.assertEqual(engine.active_trade_state, "STRUCTURE_PROTECTED")
        event = engine.events[-1]
        self.assertEqual(event.event_type, "FAVORABLE_INTERNAL_PIVOT_CONFIRMED")
        self.assertEqual(event.event_time_ns, 150)
        self.assertEqual(event.observed_time_ns, 200)
        self.assertEqual(event.previous_state, "POSITION")
        self.assertEqual(event.next_state, "STRUCTURE_PROTECTED")
        self.assertEqual(event.reason_code, "FIRST_POST_ENTRY_CONFIRMED_LOWER_HIGH")


if __name__ == "__main__":
    unittest.main()
