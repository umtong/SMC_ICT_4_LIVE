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
    def test_target_probability_precedes_expected_account_r(self) -> None:
        high_ev_lower_probability = ScoredPlan(
            "A",
            plan("high_ev", 10, 15),
            0.60,
            4.0,
            -1.0,
            1.20,
        )
        lower_ev_higher_probability = ScoredPlan(
            "B",
            plan("higher_probability", 20, 60),
            0.72,
            1.0,
            -1.0,
            0.44,
        )
        ranked = rank_scored_plans(
            [high_ev_lower_probability, lower_ev_higher_probability]
        )
        self.assertEqual(
            [item.plan.plan_id for item in ranked],
            ["higher_probability", "high_ev"],
        )

    def test_expected_account_r_breaks_equal_probability(self) -> None:
        lower_ev = ScoredPlan("A", plan("lower_ev", 10, 15), 0.65, 1.0, -1.0, 0.20)
        higher_ev = ScoredPlan("B", plan("higher_ev", 20, 60), 0.65, 2.0, -1.0, 0.50)
        ranked = rank_scored_plans([lower_ev, higher_ev])
        self.assertEqual([item.plan.plan_id for item in ranked], ["higher_ev", "lower_ev"])

    def test_causal_order_breaks_equal_quality_and_utility(self) -> None:
        first = ScoredPlan("A", plan("first", 10, 15), 0.6, 1.0, -1.0, 0.20)
        second = ScoredPlan("B", plan("second", 20, 60), 0.6, 1.0, -1.0, 0.20)
        ranked = rank_scored_plans([second, first])
        self.assertEqual([item.plan.plan_id for item in ranked], ["first", "second"])


if __name__ == "__main__":
    unittest.main()
