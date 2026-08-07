from __future__ import annotations

from datetime import date, timedelta
import unittest

from final_evidence_audit import audit_evidence
from final_evidence_audit import audit_slot_run


class FinalEvidenceAuditTest(unittest.TestCase):
    @staticmethod
    def make_run(*, trades: int = 50, slot_overrides=None, **overrides):
        slot = {
            "audit_pass": True,
            "max_unfilled_entry_intents_replayed": 1,
            "max_open_positions_replayed": 1,
            "max_entry_intents_plus_positions_replayed": 1,
            "positions_opened": trades,
            "positions_closed": trades,
            "mismatches": 0,
            "release_phase_mismatches": 0,
            "idle_at_end": True,
            "violations": [],
        }
        slot.update(slot_overrides or {})
        value = {
            "integrity_pass": True,
            "trades": trades,
            "wins": 25,
            "win_rate": 0.50,
            "active_days": 40,
            "geometric_daily_growth": 0.012,
            "largest_winner_share": 0.20,
            "max_drawdown": 0.25,
            "min_equity": 80_000.0,
            "global_slot_audit": slot,
        }
        value.update(overrides)
        return value

    @staticmethod
    def long_daily_returns():
        result = {}
        current = date(2024, 1, 1)
        end = date(2026, 6, 30)
        while current <= end:
            result[str(current)] = 0.0002
            current += timedelta(days=1)
        return result

    def make_long_run(self, **overrides):
        value = self.make_run(
            trades=500,
            wins=220,
            win_rate=0.44,
            active_days=360,
            largest_winner_share=0.05,
            stage={
                "calendar_days": 912,
                "evaluation_start": "2024-01-01",
                "evaluation_end": "2026-06-30",
            },
            daily_returns=self.long_daily_returns(),
        )
        value.update(overrides)
        slot = value["global_slot_audit"]
        slot["positions_opened"] = value["trades"]
        slot["positions_closed"] = value["trades"]
        return value

    def test_slot_positions_must_match_actual_trades_and_zero_values_are_preserved(self) -> None:
        audit = audit_slot_run(self.make_run(trades=10))
        self.assertTrue(audit["audit_pass"])

        zero = self.make_run(
            trades=0,
            slot_overrides={
                "max_unfilled_entry_intents_replayed": 0,
                "max_open_positions_replayed": 0,
                "max_entry_intents_plus_positions_replayed": 0,
                "positions_opened": 0,
                "positions_closed": 0,
            },
        )
        self.assertTrue(audit_slot_run(zero)["audit_pass"])

        audit = audit_slot_run(
            self.make_run(
                trades=10,
                slot_overrides={"positions_opened": 9, "positions_closed": 9},
            ),
        )
        self.assertFalse(audit["audit_pass"])
        self.assertFalse(audit["checks"]["positions_opened_equal_trades"])

    def test_reported_project_pass_requires_912_day_gate_and_lifecycle(self) -> None:
        stages = {
            "week-3-weak": self.make_run(trades=8),
            "week-1": self.make_run(trades=8),
            "week-2": self.make_run(trades=8),
            "continuous-30d": self.make_run(trades=20),
            "continuous-91d": self.make_run(trades=60),
            "long-2024-2026h1": self.make_long_run(),
        }
        evidence = {
            "classification": "PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS",
            "winner": "strategy:Winner",
            "shared_account": {"runs": stages},
        }
        audit = audit_evidence(evidence)
        self.assertTrue(audit["audited_project_goal_passed"])
        self.assertEqual(
            audit["classification"],
            "AUDITED_PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS",
        )

        stages["long-2024-2026h1"] = self.make_long_run(
            slot_overrides={"max_entry_intents_plus_positions_replayed": 2},
        )
        audit = audit_evidence(evidence)
        self.assertFalse(audit["audited_project_goal_passed"])
        self.assertEqual(
            audit["classification"],
            "IMPLEMENTATION_OR_EVIDENCE_ERROR_GLOBAL_LIFECYCLE_AUDIT",
        )

    def test_91_day_run_cannot_be_reported_as_project_completion(self) -> None:
        evidence = {
            "classification": "PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS",
            "winner": "strategy:Winner",
            "shared_account": {
                "runs": {"continuous-91d": self.make_run(trades=60)},
            },
        }
        audit = audit_evidence(evidence)
        self.assertFalse(audit["audited_project_goal_passed"])
        self.assertEqual(
            audit["classification"],
            "EVIDENCE_ERROR_REPORTED_PASS_FAILED_LONG_RECOMPUTED_GATE",
        )

    def test_reported_project_pass_cannot_bypass_recomputed_growth_gate(self) -> None:
        evidence = {
            "classification": "PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS",
            "winner": "strategy:Winner",
            "shared_account": {
                "runs": {
                    "long-2024-2026h1": self.make_long_run(
                        geometric_daily_growth=0.009,
                    ),
                },
            },
        }
        audit = audit_evidence(evidence)
        self.assertFalse(audit["audited_project_goal_passed"])
        self.assertEqual(
            audit["classification"],
            "EVIDENCE_ERROR_REPORTED_PASS_FAILED_LONG_RECOMPUTED_GATE",
        )


if __name__ == "__main__":
    unittest.main()
