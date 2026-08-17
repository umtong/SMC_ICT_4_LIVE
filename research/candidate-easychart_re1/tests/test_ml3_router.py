from __future__ import annotations

from types import SimpleNamespace
import unittest

from ml3_router import ScoredPlan, rank_scored_plans


def plan(name: str, interaction: int, higher: int) -> SimpleNamespace:
    return SimpleNamespace(
        plan_id=name,
        symbol="BTCUSDT",
        interaction_time_ns=interaction,
        higher_timeframe_minutes=higher,
        setup_observed_time_ns=interaction - 1,
    )


class ML3RouterTest(unittest.TestCase):
    def test_expected_account_r_precedes_causal_tie_break(self) -> None:
        early = ScoredPlan("A", plan("early", 10, 15), 0.6, 1.0, -1.0, 0.05)
        later_better = ScoredPlan("B", plan("later", 20, 60), 0.7, 1.0, -1.0, 0.40)
        ranked = rank_scored_plans([early, later_better])
        self.assertEqual([item.plan.plan_id for item in ranked], ["later", "early"])

    def test_causal_order_breaks_equal_expected_value(self) -> None:
        first = ScoredPlan("A", plan("first", 10, 15), 0.6, 1.0, -1.0, 0.20)
        second = ScoredPlan("B", plan("second", 20, 60), 0.6, 1.0, -1.0, 0.20)
        ranked = rank_scored_plans([second, first])
        self.assertEqual([item.plan.plan_id for item in ranked], ["first", "second"])


if __name__ == "__main__":
    unittest.main()
