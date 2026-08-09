from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Candidate16V2SourceContractTests(unittest.TestCase):
    def test_risk_and_global_account_contract_are_unchanged(self) -> None:
        config = json.loads((ROOT / "config_v2.json").read_text(encoding="utf-8"))
        self.assertEqual(config["risk_fraction"], 0.03)
        self.assertEqual(config["starting_nav"], 100000.0)
        source = (ROOT / "candidate_v2.py").read_text(encoding="utf-8")
        self.assertIn("research/candidate-05/backtest.py", source)
        self.assertIn("max_global_entry_or_position", source)

    def test_failure_is_frozen_before_entry(self) -> None:
        source = (ROOT / "strategy_v2.py").read_text(encoding="utf-8")
        frozen = source.index('"FAILURE_FROZEN"')
        initiative = source.index('"FAILURE_INITIATIVE_CONFIRMED"')
        submit = source.index("self._submit_entry(completed, row)")
        self.assertLess(frozen, initiative)
        self.assertLess(initiative, submit)
        self.assertIn("displayed_failure_supported", source)
        self.assertIn("displayed_acceptance_supported", source)

    def test_actual_fill_beyond_stop_is_fail_closed(self) -> None:
        source = (ROOT / "strategy_v2.py").read_text(encoding="utf-8")
        self.assertIn("ACTUAL_FILL_ALREADY_CROSSED_PLANNED_STOP", source)
        self.assertIn("self.cancel_all_orders", source)
        self.assertIn("self.close_all_positions", source)

    def test_no_score_or_outcome_risk_multiplier(self) -> None:
        source = (ROOT / "strategy_v2.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("risk_multiplier", source)
        self.assertNotIn("pnl_filter", source)
        self.assertNotIn("symbol_whitelist", source)


if __name__ == "__main__":
    unittest.main()
