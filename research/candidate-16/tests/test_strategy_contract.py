from __future__ import annotations

import inspect
import json
from pathlib import Path
import unittest

from strategy import Candidate16Strategy


class Candidate16StrategyContractTest(unittest.TestCase):
    def test_config_keeps_project_risk_fraction(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["risk_fraction"], 0.03)

    def test_no_capacity_or_score_sizing_in_submission(self) -> None:
        source = inspect.getsource(Candidate16Strategy._submit_entry)
        for forbidden in ("capacity_qty", "max_notional", "risk_multiplier", "score_multiplier"):
            self.assertNotIn(forbidden, source)

    def test_router_has_explicit_unresolved_terminal(self) -> None:
        source = inspect.getsource(Candidate16Strategy._complete_parent)
        self.assertIn("AuctionDecision.UNRESOLVED", source)
        submission = inspect.getsource(Candidate16Strategy._submit_entry)
        self.assertIn("NO_UNCONSUMED_LIQUIDITY_OBJECTIVE_AFTER_COSTS", submission)


if __name__ == "__main__":
    unittest.main()
