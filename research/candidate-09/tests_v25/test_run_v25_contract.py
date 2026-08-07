from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class V25RunnerContractTest(unittest.TestCase):
    def test_runner_has_exact_single_layer_controls(self):
        runner = (ROOT / "run_v25_direct.py").read_text(encoding="utf-8")
        self.assertIn('"baseline",\n    "no-oi",\n    "no-spot-gap",\n    "no-spot-lead",', runner)
        self.assertIn("source-auction equilibrium", runner)
        self.assertIn("completed spot price leads", runner)
        self.assertNotIn("parameter optimizer", runner.lower())

    def test_risk_evaluation_and_numerical_thresholds_remain_frozen(self):
        current = json.loads((ROOT / "config_v25.json").read_text(encoding="utf-8"))
        prior = json.loads((ROOT / "config_v24.json").read_text(encoding="utf-8"))
        for key in (
            "risk", "gate", "long_evaluation", "fixed_gate_weeks_utc", "trade",
            "flow", "breach", "positioning", "dislocation", "structure",
        ):
            self.assertEqual(current[key], prior[key], key)
        self.assertEqual(current["candidate"], "candidate-09-v25")

    def test_runner_deletes_stale_compact_evidence_before_execution(self):
        runner = (ROOT / "run_v25_direct.py").read_text(encoding="utf-8")
        self.assertIn('"event_summary.json"', runner)
        self.assertIn('(output / stale_name).unlink(missing_ok=True)', runner)
        self.assertIn('output / "trade_summary.json"', runner)


if __name__ == "__main__":
    unittest.main()
