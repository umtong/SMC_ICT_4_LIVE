"""Contracts for the Session Raid Reversal V2 bar-market execution correction."""

from __future__ import annotations

from pathlib import Path
import unittest

from aggtrade_acceptance_risk_v2 import RiskCompleteAggTradeAcceptanceStrategy
from session_raid_reversal_execution_v2 import (
    BAR_MARKET_ENTRY_RESERVE_TICKS,
    EXECUTION_RISK_REVISION,
    BarMarketRiskCompleteStrategy,
    apply_bar_market_entry_cost_contract,
)


class SessionRaidReversalExecutionV2Tests(unittest.TestCase):
    def test_missing_bar_crossing_tick_is_added_before_sizing(self) -> None:
        adjusted = apply_bar_market_entry_cost_contract(
            {
                "expected_loss_per_unit": 100.0,
                "expected_gain_per_unit": 200.0,
                "net_reward_risk": 2.0,
                "entry_slippage_reserve_per_unit": 0.1,
            },
            tick=0.1,
        )
        self.assertIsNotNone(adjusted)
        assert adjusted is not None
        self.assertAlmostEqual(float(adjusted["expected_loss_per_unit"]), 100.1)
        self.assertAlmostEqual(float(adjusted["expected_gain_per_unit"]), 199.9)
        self.assertAlmostEqual(float(adjusted["entry_slippage_reserve_per_unit"]), 0.2)
        self.assertAlmostEqual(float(adjusted["bar_market_crossing_reserve_per_unit"]), 0.1)
        self.assertAlmostEqual(float(adjusted["fill_model_slippage_reserve_per_unit"]), 0.1)
        self.assertAlmostEqual(
            float(adjusted["net_reward_risk"]),
            199.9 / 100.1,
        )
        self.assertEqual(float(adjusted["bar_market_entry_reserve_ticks"]), 2.0)
        self.assertEqual(adjusted["execution_risk_revision"], EXECUTION_RISK_REVISION)

    def test_cost_adjustment_is_idempotent(self) -> None:
        first = apply_bar_market_entry_cost_contract(
            {
                "expected_loss_per_unit": 100.0,
                "expected_gain_per_unit": 200.0,
                "net_reward_risk": 2.0,
                "entry_slippage_reserve_per_unit": 0.1,
            },
            tick=0.1,
        )
        self.assertIsNotNone(first)
        assert first is not None
        second = apply_bar_market_entry_cost_contract(first, tick=0.1)
        self.assertEqual(first, second)

    def test_nonpositive_tick_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "tick must be positive"):
            apply_bar_market_entry_cost_contract(
                {
                    "expected_loss_per_unit": 100.0,
                    "expected_gain_per_unit": 200.0,
                    "net_reward_risk": 2.0,
                },
                tick=0.0,
            )

    def test_strategy_changes_cost_only_not_risk_fraction_or_notional(self) -> None:
        self.assertTrue(
            issubclass(
                BarMarketRiskCompleteStrategy,
                RiskCompleteAggTradeAcceptanceStrategy,
            )
        )
        self.assertEqual(BAR_MARKET_ENTRY_RESERVE_TICKS, 2.0)
        source = Path(__file__).with_name("session_raid_reversal_execution_v2.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("super()._rounded_geometry", source)
        self.assertIn("super()._submit_signal", source)
        self.assertIn("super().on_order_filled", source)
        self.assertNotIn("risk_multiplier", source)
        self.assertNotIn("maximum_notional", source)
        self.assertNotIn("leverage_cap", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
