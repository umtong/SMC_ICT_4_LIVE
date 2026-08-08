from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from logic import BarObs, LogicConfig

from c10_v37_overlay import first_favorable_internal_pivot
from c10_v38_overlay import micro_pivot_protection_enabled
from c10_v38_overlay import micro_pivot_reference_contract
from c10_v38_state import ConfirmedMicroPivotProtectionEngine


def bar(ts: int, high: float, low: float, close: float) -> BarObs:
    return BarObs(
        ts_ns=ts,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=50.0,
    )


class V38PureMicroPivotTest(unittest.TestCase):
    def test_micro_environment_contract_is_exact(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V38_MICRO_PIVOT_PROTECTION": "0",
                "C10_V38_MICRO_PIVOT_REFERENCE": "CE_RETEST_EXTREME",
            },
        ):
            self.assertFalse(micro_pivot_protection_enabled())
            self.assertEqual(
                micro_pivot_reference_contract(),
                "CE_RETEST_EXTREME",
            )
        with patch.dict(
            os.environ,
            {
                "C10_V38_MICRO_PIVOT_PROTECTION": "1",
                "C10_V38_MICRO_PIVOT_REFERENCE": "EXPECTED_ENTRY",
            },
        ):
            self.assertTrue(micro_pivot_protection_enabled())
            self.assertEqual(
                micro_pivot_reference_contract(),
                "EXPECTED_ENTRY",
            )

    def test_one_minute_reference_ablation_changes_only_eligible_pivot(self) -> None:
        highs = [
            (102, 103, 17.604),
            (104, 105, 17.609),
            (106, 107, 17.590),
            (117, 118, 17.525),
        ]
        retest = first_favorable_internal_pivot(
            direction="SHORT",
            internal_highs=highs,
            internal_lows=[],
            entry_fill_ts_ns=100,
            observed_ts_ns=118,
            original_stop=17.653288,
            reference_extreme=17.649,
            current_price=17.513,
            target_price=16.956,
            atr=0.0536,
            stop_buffer_atr=0.08,
            tick_size=0.001,
        )
        entry_side = first_favorable_internal_pivot(
            direction="SHORT",
            internal_highs=highs,
            internal_lows=[],
            entry_fill_ts_ns=100,
            observed_ts_ns=118,
            original_stop=17.653288,
            reference_extreme=17.5768,
            current_price=17.513,
            target_price=16.956,
            atr=0.0536,
            stop_buffer_atr=0.08,
            tick_size=0.001,
        )
        self.assertIsNotNone(retest)
        self.assertIsNotNone(entry_side)
        assert retest is not None and entry_side is not None
        self.assertEqual(retest.pivot_level, 17.604)
        self.assertEqual(retest.protective_stop, 17.609)
        self.assertEqual(entry_side.pivot_level, 17.525)
        self.assertEqual(entry_side.protective_stop, 17.530)

    def test_entry_side_higher_low_protects_fast_eth_reversal(self) -> None:
        decision = first_favorable_internal_pivot(
            direction="LONG",
            internal_highs=[],
            internal_lows=[(106, 107, 1065.87)],
            entry_fill_ts_ns=100,
            observed_ts_ns=107,
            original_stop=1063.5519466666667,
            reference_extreme=1065.53,
            current_price=1066.49,
            target_price=1073.49,
            atr=1.2256666666666585,
            stop_buffer_atr=0.08,
            tick_size=0.01,
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.protective_stop, 1065.77)

    def test_entry_side_rejects_lower_high_still_above_short_entry(self) -> None:
        decision = first_favorable_internal_pivot(
            direction="SHORT",
            internal_highs=[(110, 111, 101.60)],
            internal_lows=[],
            entry_fill_ts_ns=100,
            observed_ts_ns=111,
            original_stop=101.69965333333334,
            reference_extreme=101.508,
            current_price=101.41,
            target_price=101.01,
            atr=0.24566666666666775,
            stop_buffer_atr=0.08,
            tick_size=0.001,
        )
        self.assertIsNone(decision)


class V38StateTest(unittest.TestCase):
    def test_micro_pivot_is_known_only_after_right_completed_bar(self) -> None:
        engine = ConfirmedMicroPivotProtectionEngine(
            LogicConfig(),
            "TEST-PERP.BINANCE",
        )
        engine.bars = [
            bar(100, 10.0, 8.0, 9.0),
            bar(200, 11.0, 8.5, 10.0),
            bar(300, 10.5, 9.0, 10.0),
        ]
        engine._confirm_micro_pivot(300)
        self.assertEqual(engine.micro_highs, [(200, 300, 11.0)])
        self.assertEqual(engine.micro_lows, [])

    def test_micro_state_event_separates_event_and_known_time(self) -> None:
        engine = ConfirmedMicroPivotProtectionEngine(
            LogicConfig(),
            "TEST-PERP.BINANCE",
        )
        engine.active_trade_id = "SCENARIO-1"
        engine.active_trade_state = "POSITION"
        engine.mark_micro_pivot_protected(
            observed_ts_ns=300,
            pivot_event_ts_ns=200,
            direction="LONG",
            pivot_level=101.0,
            reference_extreme=100.0,
            protective_stop=100.8,
            original_stop=98.0,
        )
        event = engine.events[-1]
        self.assertEqual(event.event_type, "FAVORABLE_MICRO_PIVOT_CONFIRMED")
        self.assertEqual(event.event_time_ns, 200)
        self.assertEqual(event.observed_time_ns, 300)
        self.assertEqual(event.next_state, "MICRO_STRUCTURE_PROTECTED")
        self.assertEqual(
            event.reason_code,
            "FIRST_POST_ENTRY_CONFIRMED_ONE_MINUTE_HIGHER_LOW",
        )


if __name__ == "__main__":
    unittest.main()
