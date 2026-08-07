from __future__ import annotations

import unittest

from shared_account_research import classify_30d
from shared_account_research import classify_91d
from shared_account_research import classify_three_weeks


class SharedAccountResearchGateTest(unittest.TestCase):
    @staticmethod
    def make_run(**overrides):
        value = {
            "available": True,
            "integrity_pass": True,
            "stage": {"calendar_days": 7},
            "total_return": 0.08,
            "geometric_daily_growth": 0.011,
            "max_drawdown": 0.20,
            "min_equity": 80_000.0,
            "trades": 18,
            "wins": 9,
            "losses": 9,
            "win_rate": 0.50,
            "active_days": 12,
            "largest_winner_share": 0.20,
            "global_slot_audit": {"audit_pass": True},
        }
        value.update(overrides)
        return value

    def test_three_week_gate_uses_whole_compound_not_each_week_one_percent(self) -> None:
        runs = [
            self.make_run(total_return=-0.02, trades=3, wins=1, active_days=2),
            self.make_run(total_return=0.08, trades=4, wins=2, active_days=3),
            self.make_run(total_return=0.06, trades=4, wins=2, active_days=3),
        ]
        decision = classify_three_weeks(runs)
        self.assertTrue(decision["passed"])
        self.assertTrue(decision["requirements"]["no_per_week_one_percent_gate"])
        self.assertGreater(decision["account_multiple"], 1.0)

    def test_three_week_negative_compound_is_not_promoted(self) -> None:
        runs = [
            self.make_run(total_return=-0.08, trades=3, wins=1, active_days=2),
            self.make_run(total_return=0.02, trades=3, wins=1, active_days=2),
            self.make_run(total_return=0.01, trades=3, wins=1, active_days=2),
        ]
        decision = classify_three_weeks(runs)
        self.assertFalse(decision["passed"])
        self.assertEqual(
            decision["classification"],
            "LOGIC_OR_ROBUSTNESS_FAILURE_SHARED_THREE_WEEKS",
        )

    def test_30d_gate_requires_goal_and_trade_density(self) -> None:
        run = self.make_run(
            stage={"calendar_days": 30},
            geometric_daily_growth=0.011,
            trades=16,
            wins=6,
            active_days=11,
            largest_winner_share=0.30,
        )
        decision = classify_30d(run)
        self.assertTrue(decision["passed"])

        run["trades"] = 10
        decision = classify_30d(run)
        self.assertFalse(decision["passed"])
        self.assertFalse(decision["checks"]["trades"])

    def test_91d_final_gate_requires_slot_audit_and_dispersion(self) -> None:
        run = self.make_run(
            stage={"calendar_days": 91},
            geometric_daily_growth=0.012,
            trades=60,
            wins=25,
            win_rate=25 / 60,
            active_days=40,
            largest_winner_share=0.20,
            max_drawdown=0.25,
            global_slot_audit={"audit_pass": True},
        )
        decision = classify_91d(run)
        self.assertTrue(decision["passed"])
        self.assertEqual(
            decision["classification"],
            "PROJECT_ONE_ACCOUNT_FOUR_SYMBOL_91D_GATE_PASSED",
        )

        run["global_slot_audit"] = {"audit_pass": False}
        decision = classify_91d(run)
        self.assertFalse(decision["passed"])
        self.assertFalse(decision["checks"]["global_slot_audit"])

    def test_integrity_failure_is_implementation_not_logic(self) -> None:
        run = self.make_run(available=True, integrity_pass=False)
        self.assertEqual(
            classify_30d(run)["classification"],
            "IMPLEMENTATION_OR_EVIDENCE_ERROR_SHARED_30D",
        )
        self.assertEqual(
            classify_91d(run)["classification"],
            "IMPLEMENTATION_OR_EVIDENCE_ERROR_SHARED_91D",
        )


if __name__ == "__main__":
    unittest.main()
