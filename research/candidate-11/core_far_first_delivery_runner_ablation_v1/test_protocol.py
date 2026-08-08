from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent

SPEC = importlib.util.spec_from_file_location(
    "first_delivery_run_block_protocol_test",
    HERE / "run_block.py",
)


class FirstDeliveryProtocolTest(unittest.TestCase):
    def setUp(self):
        self.protocol = json.loads((HERE / "protocol.json").read_text(encoding="utf-8"))

    def test_ablation_can_never_promote_itself(self):
        self.assertEqual(self.protocol["research_stage"], "TEMPORARY_TEST")
        for key in (
            "validation_eligible",
            "can_advance_candidate",
            "can_claim_alpha",
            "success_claim_allowed",
        ):
            self.assertIs(self.protocol[key], False)

    def test_single_variable_and_baseline_identity_are_frozen(self):
        self.assertEqual(
            self.protocol["single_changed_variable"]["name"],
            "first_delivery_realization_topology",
        )
        ids = self.protocol["baseline"]["scenario_ids"]
        self.assertEqual(len(ids), 9)
        self.assertEqual(len(set(ids)), 9)
        self.assertEqual(
            self.protocol["mechanism_contract"]["policy"],
            "SELF_FINANCING_FIRST_DELIVERY_EXTERNAL_RUNNER",
        )
        self.assertIn("same directions and entries", self.protocol["unchanged_contract"])
        self.assertIn("same inherited external target", self.protocol["unchanged_contract"])

    def test_all_blocks_are_opened_development_only(self):
        self.assertEqual(
            self.protocol["selection"]["data_use"],
            "OPENED_DEVELOPMENT_MECHANISM_ABLATION",
        )
        self.assertEqual(set(self.protocol["selection"]["blocks"]), {"D1", "D2", "D3"})
        self.assertTrue(all(
            record["role"] == "development-gate"
            for record in self.protocol["selection"]["blocks"].values()
        ))


if __name__ == "__main__":
    unittest.main()
