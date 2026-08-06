from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from authoritative_state_v2 import resolve


class AuthoritativeStateV2Test(unittest.TestCase):
    def write(self, root: Path, name: str, value: dict) -> None:
        (root / name).write_text(json.dumps(value), encoding="utf-8")

    def test_end_to_end_v2_overrides_legacy_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "current_research_state.json",
                {"classification": "PROJECT_GROWTH_GOAL_REACHED_ON_91D", "next_action": "legacy"},
            )
            self.write(
                root,
                "end_to_end_research_v2.json",
                {
                    "classification": "LOGIC_OR_ROBUSTNESS_FAILURE_SHARED_30D",
                    "winner": None,
                    "next_action": "shared failure",
                    "shared_account": {"runs": {}},
                },
            )
            state = resolve(root)
            self.assertEqual(state["source_evidence"], "end_to_end_research_v2.json")
            self.assertEqual(state["classification"], "LOGIC_OR_ROBUSTNESS_FAILURE_SHARED_30D")
            self.assertTrue(state["logic_or_robustness_failure"])

    def test_shared_smoke_error_blocks_logic_conclusion_without_end_to_end(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "shared_account_smoke.json",
                {"classification": "IMPLEMENTATION_ERROR_SHARED_RUNTIME_OR_EVIDENCE"},
            )
            state = resolve(root)
            self.assertTrue(state["implementation_or_evidence_error"])
            self.assertFalse(state["logic_or_robustness_failure"])

    def test_project_pass_requires_exact_final_classification(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "end_to_end_research_v2.json",
                {
                    "classification": "PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS",
                    "winner": "strategy:Winner",
                    "next_action": "freeze",
                    "shared_account": {"runs": {}},
                },
            )
            state = resolve(root)
            self.assertTrue(state["project_goal_passed"])
            self.assertEqual(state["selected_strategy"], "strategy:Winner")


if __name__ == "__main__":
    unittest.main()
