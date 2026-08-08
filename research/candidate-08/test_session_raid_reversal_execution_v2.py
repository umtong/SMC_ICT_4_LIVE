"""Contracts for the Session Raid Reversal V2 execution and event-ledger correction."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

from aggtrade_acceptance_risk_v2 import RiskCompleteAggTradeAcceptanceStrategy
from quote_resiliency_signals import QuoteResiliencyLogicEvent
from session_raid_reversal_execution_v2 import (
    BAR_MARKET_ENTRY_RESERVE_TICKS,
    EVENT_LEDGER_REVISION,
    EXECUTION_RISK_REVISION,
    BarMarketRiskCompleteStrategy,
    apply_bar_market_entry_cost_contract,
    direct_raid_lifecycle_events,
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
        self.assertAlmostEqual(float(adjusted["net_reward_risk"]), 199.9 / 100.1)
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

    def test_legacy_single_event_becomes_three_causal_events(self) -> None:
        legacy = QuoteResiliencyLogicEvent(
            scenario_id="raid-1",
            symbol="BTCUSDT",
            instrument_id="BTCUSDT-PERP.BINANCE",
            event_type="LEGACY_CONFIRMED",
            event_time_ns=200,
            observed_time_ns=200,
            previous_state="HTF_DRAW_ARMED",
            next_state="CONFIRMED",
            reason_code="LEGACY",
            reference_price=100.0,
            details={"scenario_family": "H4_DRAW_DIRECT_SESSION_RAID_REVERSAL"},
        )
        signal = SimpleNamespace(
            events=(legacy,),
            interaction_time_ns=100,
            response_time_ns=100,
            signal_time_ns=110,
            boundary_level=99.0,
            entry_reference=100.0,
        )
        events = direct_raid_lifecycle_events(signal)
        self.assertEqual(len(events), 3)
        self.assertEqual(
            [event.event_type for event in events],
            [
                "SESSION_RAID_OBSERVED",
                "SESSION_RAID_RECLAIM_CONFIRMED",
                "NEXT_EXECUTION_BUCKET_OBSERVED",
            ],
        )
        self.assertEqual([event.observed_time_ns for event in events], [100, 100, 110])
        self.assertEqual(
            [(event.previous_state, event.next_state) for event in events],
            [
                ("HTF_DRAW_ARMED", "SESSION_RAID_OBSERVED"),
                ("SESSION_RAID_OBSERVED", "RAID_RECLAIM_CONFIRMED"),
                ("RAID_RECLAIM_CONFIRMED", "CONFIRMED"),
            ],
        )
        self.assertTrue(
            all(
                event.details["event_ledger_revision"] == EVENT_LEDGER_REVISION
                for event in events
            )
        )

    def test_noncausal_lifecycle_is_rejected(self) -> None:
        legacy = QuoteResiliencyLogicEvent(
            scenario_id="raid-1",
            symbol="BTCUSDT",
            instrument_id="BTCUSDT-PERP.BINANCE",
            event_type="LEGACY_CONFIRMED",
            event_time_ns=100,
            observed_time_ns=100,
            previous_state="HTF_DRAW_ARMED",
            next_state="CONFIRMED",
            reason_code="LEGACY",
        )
        signal = SimpleNamespace(
            events=(legacy,),
            interaction_time_ns=100,
            response_time_ns=110,
            signal_time_ns=110,
            boundary_level=99.0,
            entry_reference=100.0,
        )
        with self.assertRaisesRegex(ValueError, "not causal"):
            direct_raid_lifecycle_events(signal)

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
