from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
CORE_ROOT = HERE.parent / "core_far_continuous_v1"
for path in (HERE, CORE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from run_block import validate_protocol  # noqa: E402


class StructureTransferProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = json.loads((HERE / "protocol.json").read_text(encoding="utf-8"))
        cls.core = json.loads((CORE_ROOT / "protocol.json").read_text(encoding="utf-8"))

    def test_ablation_can_never_promote_itself(self) -> None:
        validate_protocol(self.protocol, self.core)
        self.assertEqual(self.protocol["research_stage"], "TEMPORARY_TEST")
        self.assertFalse(self.protocol["validation_eligible"])
        self.assertFalse(self.protocol["can_advance_candidate"])
        self.assertFalse(self.protocol["can_claim_alpha"])
        self.assertFalse(self.protocol["success_claim_allowed"])

    def test_single_variable_and_scenario_identity_are_frozen(self) -> None:
        self.assertEqual(
            self.protocol["single_changed_variable"]["name"],
            "post_entry_risk_owner",
        )
        scenario_ids = self.protocol["baseline"]["scenario_ids"]
        self.assertEqual(len(scenario_ids), 9)
        self.assertEqual(len(set(scenario_ids)), 9)
        self.assertEqual(
            self.protocol["selection"]["blocks"],
            self.core["selection"]["blocks"],
        )
        exclusions = " ".join(
            self.protocol["single_changed_variable"]["not_used"]
        ).lower()
        self.assertIn("mfe", exclusions)
        self.assertIn("breakeven", exclusions)
        self.assertIn("target change", exclusions)
        self.assertIn("entry change", exclusions)


if __name__ == "__main__":
    unittest.main()
