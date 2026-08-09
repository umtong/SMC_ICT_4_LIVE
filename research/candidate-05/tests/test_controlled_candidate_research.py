from __future__ import annotations

import unittest

from controlled_candidate_research import classify_continuous_control


class ControlledCandidateResearchTest(unittest.TestCase):
    @staticmethod
    def make_run(*, growth: float, branch_pnl: float, branch_trades: int = 5):
        return {
            "available": True,
            "integrity_checks": {
                "engine_is_nautilus": True,
                "positive_nav": True,
                "no_liquidation": True,
                "no_order_rejections": True,
                "no_order_denials": True,
                "single_entry_intent": True,
                "single_position": True,
                "nautilus_positions_consistent": True,
                "nautilus_orders_consistent": True,
            },
            "geometric_daily_growth": growth,
            "scenario_metrics": {
                "BRANCH": {
                    "trades": branch_trades,
                    "wins": max(0, branch_trades - 2),
                    "net_pnl": branch_pnl,
                },
            },
        }

    def test_candidate_must_beat_same_period_control_and_growth_goal(self) -> None:
        baseline = self.make_run(growth=0.008, branch_pnl=0.0, branch_trades=0)
        candidate = self.make_run(growth=0.011, branch_pnl=2500.0)
        decision = classify_continuous_control(
            baseline=baseline,
            candidate=candidate,
            branch="BRANCH",
            require_growth_goal=True,
        )
        self.assertTrue(decision["passed"])
        self.assertEqual(decision["classification"], "LOGIC_SCREEN_PASSED_CONTINUOUS_30D")

        candidate["geometric_daily_growth"] = 0.007
        decision = classify_continuous_control(
            baseline=baseline,
            candidate=candidate,
            branch="BRANCH",
            require_growth_goal=True,
        )
        self.assertEqual(decision["classification"], "LOGIC_FAILURE_DID_NOT_IMPROVE_CONTROL_30D")

    def test_positive_branch_below_whole_period_goal_is_not_promoted(self) -> None:
        baseline = self.make_run(growth=0.004, branch_pnl=0.0, branch_trades=0)
        candidate = self.make_run(growth=0.009, branch_pnl=2500.0)
        decision = classify_continuous_control(
            baseline=baseline,
            candidate=candidate,
            branch="BRANCH",
            require_growth_goal=True,
        )
        self.assertFalse(decision["passed"])
        self.assertEqual(decision["classification"], "LOGIC_FAILURE_BELOW_GOAL_ON_CONTINUOUS_30D")

    def test_nonpositive_incremental_branch_is_rejected_even_with_high_growth(self) -> None:
        baseline = self.make_run(growth=0.008, branch_pnl=0.0, branch_trades=0)
        candidate = self.make_run(growth=0.012, branch_pnl=-100.0)
        decision = classify_continuous_control(
            baseline=baseline,
            candidate=candidate,
            branch="BRANCH",
            require_growth_goal=True,
        )
        self.assertFalse(decision["passed"])
        self.assertEqual(
            decision["classification"],
            "LOGIC_FAILURE_NONPOSITIVE_INCREMENTAL_EXPECTANCY_30D",
        )


if __name__ == "__main__":
    unittest.main()
