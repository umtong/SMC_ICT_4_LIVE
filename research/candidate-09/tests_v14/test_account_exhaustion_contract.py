from __future__ import annotations

import unittest
from decimal import Decimal

from nautilus_strategy import sizing_failure_reason
from run import (
    DetailedRun,
    RunOutcome,
    account_exhaustion_signal_count,
    evaluate_long,
    sizing_infeasible_signal_count,
)


def exhausted_detail() -> DetailedRun:
    outcome = RunOutcome(
        run_id="baseline:long-btc",
        variant="baseline",
        segment="long-btc",
        start_ns=0,
        end_ns=365 * 86_400_000_000_000,
        calendar_days=365.0,
        starting_nav=100000.0,
        ending_nav=0.5,
        total_return=-0.999995,
        daily_geometric_return=-0.03,
        trades=700,
        wins=300,
        losses=400,
        win_rate=300 / 700,
        profit_factor=0.8,
        expectancy_r=-0.05,
        max_drawdown=0.999995,
        maximum_consecutive_losses=12,
        largest_profit_share=0.02,
        reversal_trades=700,
        continuation_trades=0,
        rejected_orders=0,
        time_exits=0,
        missing_feature_bars=0,
        open_position_at_stop=False,
        native_account_final=1000.0,
        native_expected_final=1000.0,
        accounting_error=0.0,
        implementation_status="OK",
    )
    return DetailedRun(
        outcome=outcome,
        events=[
            {
                "reason_code": "ACCOUNT_BELOW_COST_ONLY_MINIMUM_QUANTITY",
                "event_type": "ENTRY_SKIPPED",
            },
            {
                "reason_code": "RISK_BUDGET_BELOW_SIGNAL_MINIMUM_QUANTITY",
                "event_type": "ENTRY_SKIPPED",
            },
        ],
        trades=[],
        fills=[],
    )


class AccountExhaustionContractTest(unittest.TestCase):
    def reason(self, message: str, *, nav: str, risk: str = "0.03") -> str | None:
        return sizing_failure_reason(
            message,
            nav=Decimal(nav),
            risk_fraction=Decimal(risk),
            entry_price=Decimal("100000"),
            cost_rate=Decimal("0.00075"),
            minimum_quantity=Decimal("0.001"),
        )

    def test_only_known_and_economically_consistent_sizing_failures_are_swallowed(self):
        self.assertEqual(
            self.reason("risk budget is below one exchange quantity increment", nav="1000"),
            "RISK_BUDGET_BELOW_SIGNAL_MINIMUM_QUANTITY",
        )
        self.assertEqual(
            self.reason("risk budget is below one exchange quantity increment", nav="1"),
            "ACCOUNT_BELOW_COST_ONLY_MINIMUM_QUANTITY",
        )
        self.assertEqual(
            self.reason("NAV must be positive and risk_fraction must be in (0, 0.03]", nav="0"),
            "ACCOUNT_NAV_NON_POSITIVE",
        )
        self.assertIsNone(
            self.reason(
                "NAV must be positive and risk_fraction must be in (0, 0.03]",
                nav="1000",
                risk="0.04",
            ),
        )
        self.assertIsNone(self.reason("unexpected arithmetic failure", nav="1000"))

    def test_true_account_exhaustion_is_logic_failure_not_implementation_error(self):
        detail = exhausted_detail()
        config = {
            "gate": {"maximum_single_trade_profit_share": 0.35},
            "long_evaluation": {
                "success_daily_geometric_return": 0.01,
                "minimum_trades_per_calendar_day": 0.5,
                "minimum_active_months": 30,
                "maximum_drawdown": 0.30,
            },
        }
        passed, report = evaluate_long(config, detail)
        self.assertFalse(passed)
        self.assertTrue(report["checks"]["implementation_ok"])
        self.assertFalse(report["checks"]["account_remained_recoverable"])
        self.assertEqual(report["sizing_infeasible_signals"], 2)
        self.assertEqual(report["account_exhaustion_signals"], 1)
        self.assertEqual(sizing_infeasible_signal_count([detail]), 2)
        self.assertEqual(account_exhaustion_signal_count([detail]), 1)


if __name__ == "__main__":
    unittest.main()
