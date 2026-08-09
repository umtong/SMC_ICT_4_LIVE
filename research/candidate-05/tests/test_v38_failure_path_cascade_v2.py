from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from v38_failure_path_cascade_v2 import CASCADE_SCHEMA
from v38_failure_path_cascade_v2 import write_json


class V38FailurePathCascadeSchemaRepairTest(unittest.TestCase):
    def test_component_payload_always_receives_cascade_schema(self) -> None:
        payload = {
            "schema": "candidate-05-v38-failure-path-diagnostic-v1",
            "method": {"engine": "OBSERVATIONAL_COMPLETED_BAR_DIAGNOSTIC_ONLY"},
            "cases": [],
            "losing_original_v38_cases": {"component_cascade": {}},
            "nonnegative_original_v38_cases": {"component_cascade": {}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            write_json(path, payload)
            persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["schema"], CASCADE_SCHEMA)
        self.assertEqual(
            persisted["schema_repair"],
            "LABEL_ONLY_NO_MARKET_OBSERVATION_OR_PREDICATE_CHANGED",
        )

    def test_non_component_payload_keeps_its_existing_schema(self) -> None:
        payload = {"schema": "unrelated", "value": 1}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            write_json(path, payload)
            persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted, payload)


if __name__ == "__main__":
    unittest.main()
