from __future__ import annotations

import inspect
import json
from pathlib import Path
import unittest

from strategy import Candidate16Strategy


ROOT = Path(__file__).resolve().parents[1]


class Candidate16V2StrategyContractTest(unittest.TestCase):
    def test_project_risk_and_global_execution_contracts_remain_fixed(self) -> None:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["risk_fraction"], 0.03)
        self.assertEqual(config["strategy"]["source_horizons_minutes"], [15, 60, 1440])
        self.assertFalse(config["strategy"]["enable_rejection"])
        self.assertFalse(config["strategy"]["enable_acceptance"])
        self.assertEqual(config["strategy"]["min_target_net_r"], 1.2)

    def test_micro_pivot_context_is_disabled(self) -> None:
        source = inspect.getsource(Candidate16Strategy._confirm_pivots)
        self.assertNotIn("_add_pool", source)
        detect = inspect.getsource(Candidate16Strategy._detect_sweep)
        self.assertIn("_source_levels", detect)
        self.assertNotIn("active_pools", detect)

    def test_state_transition_and_entry_trigger_are_separate(self) -> None:
        source = inspect.getsource(Candidate16Strategy._process_pending)
        self.assertIn("advance", source)
        self.assertIn("ResolutionDecision.PENDING", source)
        self.assertIn("_submit_resolution_entry", source)
        router_source = (ROOT / "accepted_failure_router.py").read_text(encoding="utf-8")
        self.assertIn("Acceptance, failure, and\n    entry therefore cannot be asserted by the same completed bar", router_source)

    def test_stop_and_target_belong_to_the_failed_source_auction(self) -> None:
        source = inspect.getsource(Candidate16Strategy._submit_resolution_entry)
        self.assertIn("min(level.price, state.failure_low", source)
        self.assertIn("max(level.price, state.failure_high", source)
        self.assertIn("SOURCE_RANGE_MIDPOINT", source)
        self.assertIn("SOURCE_OPPOSITE_EDGE", source)
        self.assertNotIn("FALLBACK_CAUSAL_EXPANSION", source)

    def test_no_capacity_or_score_based_position_reduction(self) -> None:
        source = inspect.getsource(Candidate16Strategy._submit_resolution_entry)
        for forbidden in (
            "capacity_qty",
            "max_notional",
            "risk_multiplier",
            "score_multiplier",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("equity * self.config.risk_fraction", source)
        self.assertIn("risk_budget / planned_loss", source)


if __name__ == "__main__":
    unittest.main()
