from __future__ import annotations

import unittest

from v20_impact_math import impact_adjusted_ledger, solve_risk_quantity


class ImpactMathTests(unittest.TestCase):
    def test_fixed_point_respects_risk_budget(self) -> None:
        solved = solve_risk_quantity(
            risk_budget=3000.0, entry=100.0, stop=98.0, taker_fee=0.0007,
            base_impact=0.05, atr=1.5, liquidity_notional=100_000.0,
        )
        self.assertIsNotNone(solved)
        assert solved is not None
        self.assertGreater(solved.impact_per_side, 0.05)
        self.assertAlmostEqual(solved.quantity * solved.per_unit_loss, 3000.0, places=6)

    def test_deeper_market_increases_risk_quantity_without_cap(self) -> None:
        thin = solve_risk_quantity(
            risk_budget=3000.0, entry=100.0, stop=98.0, taker_fee=0.0007,
            base_impact=0.05, atr=1.5, liquidity_notional=20_000.0,
        )
        deep = solve_risk_quantity(
            risk_budget=3000.0, entry=100.0, stop=98.0, taker_fee=0.0007,
            base_impact=0.05, atr=1.5, liquidity_notional=2_000_000.0,
        )
        assert thin is not None and deep is not None
        self.assertGreater(thin.impact_per_side, deep.impact_per_side)
        self.assertLess(thin.quantity, deep.quantity)

    def test_impact_ledger_never_improves_nav(self) -> None:
        result = impact_adjusted_ledger(
            starting_nav=100_000.0,
            ending_nav=104_000.0,
            daily_nav={"2023-10-16": 102_000.0, "2023-10-17": 104_000.0},
            equity_curve=[
                {"ts_ns": 1, "equity": 100_000.0},
                {"ts_ns": 2_000_000_000, "equity": 102_000.0},
                {"ts_ns": 90_000_000_000_000, "equity": 104_000.0},
            ],
            trades=[{
                "closed_ts_ns": 2_000_000_000,
                "conservative_impact_cost": 500.0,
            }],
            tick_max_drawdown=0.01,
        )
        self.assertEqual(result["impact_adjustment_total"], 500.0)
        self.assertEqual(result["impact_adjusted_ending_nav"], 103_500.0)
        self.assertLess(result["impact_adjusted_net_return"], 0.04)
        self.assertGreaterEqual(result["impact_adjusted_intraday_max_drawdown"], 0.01)


if __name__ == "__main__":
    unittest.main()
