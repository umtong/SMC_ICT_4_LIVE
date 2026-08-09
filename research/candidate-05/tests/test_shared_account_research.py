from __future__ import annotations

from datetime import date, timedelta
import unittest

from shared_account_research import LONG_MIN_ACTIVE_DAYS
from shared_account_research import LONG_MIN_TRADES
from shared_account_research import LONG_MIN_WINS
from shared_account_research import classify_30d
from shared_account_research import classify_91d
from shared_account_research import classify_long
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
            "daily_returns": {},
        }
        value.update(overrides)
        return value

    @staticmethod
    def long_daily_returns(*, positive_months: int = 18):
        values = {}
        start = date(2024, 1, 1)
        end = date(2026, 6, 30)
        current = start
        while current <= end:
            month_index = (current.year - 2024) * 12 + current.month - 1
            values[str(current)] = 0.0005 if month_index < positive_months else -0.0001
            current += timedelta(days=1)
        return values

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
        self.assertEqual(decision["classification"], "LOGIC_OR_ROBUSTNESS_FAILURE_SHARED_THREE_WEEKS")

    def test_30d_gate_requires_goal_and_trade_density(self) -> None:
        run = self.make_run(
            stage={"calendar_days": 30},
            geometric_daily_growth=0.011,
            trades=16,
            wins=6,
            active_days=11,
            largest_winner_share=0.30,
        )
        self.assertTrue(classify_30d(run)["passed"])
        run["trades"] = 10
        decision = classify_30d(run)
        self.assertFalse(decision["passed"])
        self.assertFalse(decision["checks"]["trades"])

    def test_91d_is_promotion_not_project_completion(self) -> None:
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
        self.assertEqual(decision["classification"], "SHARED_ACCOUNT_91D_PROMOTION_PASSED")
        self.assertTrue(decision["project_goal_not_yet_reached"])

    def test_long_gate_requires_exact_2024_through_2026h1_and_dispersion(self) -> None:
        run = self.make_run(
            stage={
                "calendar_days": 912,
                "evaluation_start": "2024-01-01",
                "evaluation_end": "2026-06-30",
            },
            geometric_daily_growth=0.012,
            trades=LONG_MIN_TRADES,
            wins=LONG_MIN_WINS,
            win_rate=0.40,
            active_days=LONG_MIN_ACTIVE_DAYS,
            largest_winner_share=0.08,
            max_drawdown=0.25,
            global_slot_audit={"audit_pass": True},
            daily_returns=self.long_daily_returns(positive_months=18),
        )
        decision = classify_long(run)
        self.assertTrue(decision["passed"])
        self.assertEqual(
            decision["classification"],
            "PROJECT_ONE_ACCOUNT_FOUR_SYMBOL_LONG_2024_2026H1_GATE_PASSED",
        )

        run["stage"] = {
            "calendar_days": 91,
            "evaluation_start": "2024-03-01",
            "evaluation_end": "2024-05-30",
        }
        decision = classify_long(run)
        self.assertFalse(decision["passed"])
        self.assertFalse(decision["checks"]["calendar_days_912"])

    def test_integrity_failure_is_implementation_not_logic(self) -> None:
        run = self.make_run(available=True, integrity_pass=False)
        self.assertEqual(classify_30d(run)["classification"], "IMPLEMENTATION_OR_EVIDENCE_ERROR_SHARED_30D")
        self.assertEqual(classify_91d(run)["classification"], "IMPLEMENTATION_OR_EVIDENCE_ERROR_SHARED_91D")
        self.assertEqual(
            classify_long(run)["classification"],
            "IMPLEMENTATION_OR_EVIDENCE_ERROR_SHARED_LONG_2024_2026H1",
        )


if __name__ == "__main__":
    unittest.main()
