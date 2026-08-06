from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from authoritative_state_v3 import resolve


class AuthoritativeStateV3Test(unittest.TestCase):
    def write(self, root: Path, name: str, value: dict) -> None:
        (root / name).write_text(json.dumps(value), encoding="utf-8")

    def test_independent_audited_pass_has_final_authority(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "end_to_end_research_v2.json",
                {
                    "classification": "PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS",
                    "winner": "strategy:Winner",
                    "next_action": "reported",
                    "shared_account": {"runs": {}},
                },
            )
            self.write(
                root,
                "final_evidence_audit.json",
                {
                    "classification": "AUDITED_PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS",
                    "audited_project_goal_passed": True,
                    "selected_strategy": "strategy:Winner",
                    "lifecycle_audit_pass": True,
                    "next_action": "freeze",
                },
            )
            state = resolve(root)
            self.assertTrue(state["project_goal_passed"])
            self.assertTrue(state["audited_project_goal_passed"])
            self.assertEqual(state["source_evidence"], "final_evidence_audit.json")

    def test_audit_implementation_error_revokes_reported_pass(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "end_to_end_research_v2.json",
                {
                    "classification": "PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS",
                    "winner": "strategy:Winner",
                    "next_action": "reported",
                    "shared_account": {"runs": {}},
                },
            )
            self.write(
                root,
                "final_evidence_audit.json",
                {
                    "classification": "IMPLEMENTATION_OR_EVIDENCE_ERROR_GLOBAL_LIFECYCLE_AUDIT",
                    "audited_project_goal_passed": False,
                    "selected_strategy": None,
                    "lifecycle_audit_pass": False,
                    "next_action": "repair",
                },
            )
            state = resolve(root)
            self.assertFalse(state["project_goal_passed"])
            self.assertTrue(state["implementation_or_evidence_error"])
            self.assertIsNone(state["selected_strategy"])

    def test_missing_audit_never_yields_audited_pass(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "end_to_end_research_v2.json",
                {
                    "classification": "LOGIC_OR_ROBUSTNESS_FAILURE_SHARED_30D",
                    "winner": None,
                    "next_action": "improve",
                    "shared_account": {"runs": {}},
                },
            )
            state = resolve(root)
            self.assertFalse(state["audited_project_goal_passed"])
            self.assertFalse(state["project_goal_passed"])


if __name__ == "__main__":
    unittest.main()
