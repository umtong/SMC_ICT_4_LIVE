from __future__ import annotations

import unittest

from final_evidence_audit import audit_evidence
from final_evidence_audit import audit_slot_run


class FinalEvidenceAuditTest(unittest.TestCase):
    def run(self, *, trades: int = 50, slot_overrides=None, **overrides):
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

    def test_slot_positions_must_match_actual_trades(self) -> None:
        audit = audit_slot_run(self.run(trades=10))
        self.assertTrue(audit["audit_pass"])

        audit = audit_slot_run(
            self.run(
                trades=10,
                slot_overrides={"positions_opened": 9, "positions_closed": 9},
            ),
        )
        self.assertFalse(audit["audit_pass"])
        self.assertFalse(audit["checks"]["positions_opened_equal_trades"])

    def test_reported_project_pass_requires_replayed_lifecycle_and_gate(self) -> None:
        stages = {
            "week-3-weak": self.run(trades=8),
            "week-1": self.run(trades=8),
            "week-2": self.run(trades=8),
            "continuous-30d": self.run(trades=20),
            "continuous-91d": self.run(trades=60),
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

        stages["continuous-91d"] = self.run(
            trades=60,
            slot_overrides={"max_entry_intents_plus_positions_replayed": 2},
        )
        audit = audit_evidence(evidence)
        self.assertFalse(audit["audited_project_goal_passed"])
        self.assertEqual(
            audit["classification"],
            "IMPLEMENTATION_OR_EVIDENCE_ERROR_GLOBAL_LIFECYCLE_AUDIT",
        )

    def test_reported_project_pass_cannot_bypass_recomputed_growth_gate(self) -> None:
        stages = {
            "continuous-91d": self.run(
                trades=60,
                geometric_daily_growth=0.009,
            ),
        }
        evidence = {
            "classification": "PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS",
            "winner": "strategy:Winner",
            "shared_account": {"runs": stages},
        }
        audit = audit_evidence(evidence)
        self.assertFalse(audit["audited_project_goal_passed"])
        self.assertEqual(
            audit["classification"],
            "EVIDENCE_ERROR_REPORTED_PASS_FAILED_RECOMPUTED_GATE",
        )


if __name__ == "__main__":
    unittest.main()
