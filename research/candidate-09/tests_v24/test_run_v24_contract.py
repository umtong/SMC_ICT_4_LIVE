from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class V24RunnerContractTest(unittest.TestCase):
    def test_runner_has_exact_single_layer_controls(self):
        runner = (ROOT / "run_v24_direct.py").read_text(encoding="utf-8")
        self.assertIn('"baseline",\n    "no-oi",\n    "no-index-gap",\n    "no-reclaim",', runner)
        self.assertIn("index-anchored", runner)
        self.assertIn("frozen fair basis", runner)

    def test_risk_and_evaluation_contracts_remain_frozen(self):
        current = json.loads((ROOT / "config_v24.json").read_text(encoding="utf-8"))
        prior = json.loads((ROOT / "config_v23.json").read_text(encoding="utf-8"))
        for key in ("risk", "gate", "long_evaluation", "fixed_gate_weeks_utc", "trade"):
            self.assertEqual(current[key], prior[key], key)
        self.assertEqual(current["candidate"], "candidate-09-v24")
        self.assertEqual(current["dislocation"]["confirmation_timeout_bars"], 4)


if __name__ == "__main__":
    unittest.main()
