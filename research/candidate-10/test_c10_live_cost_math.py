from __future__ import annotations

import unittest

from c10_live_cost_math import (
    LiveImpactLedger,
    impact_adjusted_ledger,
    live_ledger_diagnostics,
    solve_risk_quantity,
)


class ImpactMathTests(unittest.TestCase):
    def test_fixed_point_respects_risk_budget(self) -> None:
        solved = solve_risk_quantity(
            risk_budget=3000.0,
            entry=100.0,
            stop=98.0,
            taker_fee=0.0007,
            base_impact=0.05,
            atr=1.5,
            liquidity_notional=100_000.0,
        )
        self.assertIsNotNone(solved)
        assert solved is not None
        self.assertGreater(solved.impact_per_side, 0.05)
        self.assertAlmostEqual(
            solved.quantity * solved.per_unit_loss,
            3000.0,
            places=6,
        )

    def test_deeper_market_increases_risk_quantity_without_cap(self) -> None:
        thin = solve_risk_quantity(
            risk_budget=3000.0,
            entry=100.0,
            stop=98.0,
            taker_fee=0.0007,
            base_impact=0.05,
            atr=1.5,
            liquidity_notional=20_000.0,
        )
        deep = solve_risk_quantity(
            risk_budget=3000.0,
            entry=100.0,
            stop=98.0,
            taker_fee=0.0007,
            base_impact=0.05,
            atr=1.5,
            liquidity_notional=2_000_000.0,
        )
        assert thin is not None and deep is not None
        self.assertGreater(thin.impact_per_side, deep.impact_per_side)
        self.assertLess(thin.quantity, deep.quantity)

    def test_live_ledger_reduces_later_risk_nav(self) -> None:
        ledger = LiveImpactLedger()
        first_nav = ledger.conservative_equity(100_000.0)
        self.assertEqual(first_nav, 100_000.0)
        cost = ledger.debit(
            quantity=10.0,
            impact_per_unit=25.0,
            ts_ns=10,
            role="ENTRY",
            scenario_id="S1",
        )
        self.assertEqual(cost, 250.0)
        self.assertEqual(ledger.cumulative_cost, 250.0)
        self.assertEqual(ledger.conservative_equity(99_000.0), 98_750.0)
        self.assertAlmostEqual(
            ledger.conservative_equity(99_000.0) * 0.03,
            2_962.5,
        )

    def test_impact_ledger_splits_entry_and_exit_timing(self) -> None:
        result = impact_adjusted_ledger(
            starting_nav=100_000.0,
            ending_nav=104_000.0,
            daily_nav={
                "1970-01-01": 102_000.0,
                "1970-01-02": 104_000.0,
            },
            equity_curve=[
                {"ts_ns": 1, "equity": 100_000.0},
                {"ts_ns": 2_000_000_000, "equity": 102_000.0},
                {"ts_ns": 90_000_000_000_000, "equity": 104_000.0},
            ],
            trades=[
                {
                    "opened_ts_ns": 2_000_000_000,
                    "closed_ts_ns": 90_000_000_000_000,
                    "conservative_entry_impact_cost": 200.0,
                    "conservative_exit_impact_cost": 300.0,
                    "conservative_impact_cost": 500.0,
                },
            ],
            tick_max_drawdown=0.01,
        )
        self.assertEqual(result["impact_adjustment_total"], 500.0)
        self.assertEqual(result["impact_adjusted_ending_nav"], 103_500.0)
        self.assertEqual(result["impact_debit_event_count"], 2)
        self.assertEqual(
            result["impact_debit_timing"],
            "ACTUAL_ENTRY_AND_EXIT_FILL_TIMESTAMPS",
        )
        adjusted_curve = result["impact_adjusted_equity_curve"]
        self.assertEqual(adjusted_curve[1]["equity"], 101_800.0)
        self.assertEqual(adjusted_curve[2]["equity"], 103_500.0)
        self.assertGreaterEqual(
            result["impact_adjusted_intraday_max_drawdown"],
            0.01,
        )

    def test_live_ledger_diagnostics_detects_optimistic_second_budget(self) -> None:
        trades = [
            {
                "scenario_id": "S1",
                "opened_ts_ns": 1,
                "start_equity": 100_000.0,
                "impact_ledger_cost_before_entry": 0.0,
                "planned_loss_budget_nav_basis": 100_000.0,
                "conservative_start_equity": 100_000.0,
                "planned_loss_budget": 3_000.0,
                "planned_loss": 2_999.0,
                "conservative_impact_cost": 500.0,
                "end_equity": 98_000.0,
                "conservative_end_equity": 97_500.0,
            },
            {
                "scenario_id": "S2",
                "opened_ts_ns": 2,
                "start_equity": 98_000.0,
                # Historical defect: prior impact omitted from basis.
                "impact_ledger_cost_before_entry": 0.0,
                "planned_loss_budget_nav_basis": 98_000.0,
                "conservative_start_equity": 98_000.0,
                "planned_loss_budget": 2_940.0,
                "planned_loss": 2_939.0,
                "conservative_impact_cost": 400.0,
                "end_equity": 96_000.0,
                "conservative_end_equity": 95_100.0,
            },
        ]
        result = live_ledger_diagnostics(
            trades=trades,
            risk_fraction=0.03,
            adjusted_ending_nav=95_100.0,
        )
        self.assertEqual(result["risk_budget_violation_count"], 1)
        self.assertEqual(
            result["risk_budget_violations"][0]["scenario_id"],
            "S2",
        )

    def test_live_ledger_diagnostics_accepts_cost_after_budget(self) -> None:
        trades = [
            {
                "scenario_id": "S1",
                "opened_ts_ns": 1,
                "start_equity": 100_000.0,
                "impact_ledger_cost_before_entry": 0.0,
                "planned_loss_budget_nav_basis": 100_000.0,
                "conservative_start_equity": 100_000.0,
                "planned_loss_budget": 3_000.0,
                "planned_loss": 2_999.0,
                "conservative_impact_cost": 500.0,
                "end_equity": 98_000.0,
                "conservative_end_equity": 97_500.0,
            },
            {
                "scenario_id": "S2",
                "opened_ts_ns": 2,
                "start_equity": 98_000.0,
                "impact_ledger_cost_before_entry": 500.0,
                "planned_loss_budget_nav_basis": 97_500.0,
                "conservative_start_equity": 97_500.0,
                "planned_loss_budget": 2_925.0,
                "planned_loss": 2_924.0,
                "conservative_impact_cost": 400.0,
                "end_equity": 96_000.0,
                "conservative_end_equity": 95_100.0,
            },
        ]
        result = live_ledger_diagnostics(
            trades=trades,
            risk_fraction=0.03,
            adjusted_ending_nav=95_100.0,
        )
        self.assertEqual(result["risk_budget_violation_count"], 0)
        self.assertTrue(result["recorded_vs_reported_ending_nav_match"])

    def test_legacy_close_time_cost_remains_supported(self) -> None:
        result = impact_adjusted_ledger(
            starting_nav=100_000.0,
            ending_nav=100_000.0,
            daily_nav={"1970-01-01": 100_000.0},
            equity_curve=[{"ts_ns": 2_000_000_000, "equity": 100_000.0}],
            trades=[
                {
                    "closed_ts_ns": 2_000_000_000,
                    "conservative_impact_cost": 500.0,
                },
            ],
            tick_max_drawdown=0.0,
        )
        self.assertEqual(result["impact_adjustment_total"], 500.0)
        self.assertEqual(result["impact_debit_event_count"], 1)
        self.assertEqual(result["impact_adjusted_ending_nav"], 99_500.0)


if __name__ == "__main__":
    unittest.main()
