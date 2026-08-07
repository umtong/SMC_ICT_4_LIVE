from __future__ import annotations

from pathlib import Path
import unittest

from runner_materializer import (
    NEW_ORDER_BLOCK,
    NEW_POST_GATE_BLOCK,
    OLD_ORDER_BLOCK,
    OLD_POST_GATE_BLOCK,
    materialize_runner_source,
)
from semantic_execution import MARKET_ENTRY_SENTINEL_NS


ROOT = Path(__file__).resolve().parent


class SemanticExecutionBoundaryTests(unittest.TestCase):
    def test_exact_frozen_runner_boundaries_materialize_and_compile(self):
        source = (ROOT / "run_leadership_scdam_base.py").read_text(encoding="utf-8")
        materialized = materialize_runner_source(source)
        self.assertNotIn(OLD_ORDER_BLOCK, materialized)
        self.assertNotIn(OLD_POST_GATE_BLOCK, materialized)
        self.assertIn(NEW_ORDER_BLOCK, materialized)
        self.assertIn(NEW_POST_GATE_BLOCK, materialized)
        self.assertEqual(materialized.count("candidate-13-market-parent"), 1)
        self.assertEqual(materialized.count("candidate-13-post-gate"), 1)
        compile(materialized, "run_leadership_scdam_base.py", "exec")

    def test_contract_drift_fails_closed(self):
        with self.assertRaises(RuntimeError):
            materialize_runner_source("order factory boundary changed")

    def test_market_plan_has_no_live_gtd_expiry(self):
        self.assertEqual(MARKET_ENTRY_SENTINEL_NS, 946684800000000000)


if __name__ == "__main__":
    unittest.main()
