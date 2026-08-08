from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Candidate16V5BExecutionTests(unittest.TestCase):
    def test_fok_limit_replaces_only_stop_trigger(self) -> None:
        source = (ROOT / "strategy_v5b.py").read_text(encoding="utf-8")
        self.assertIn("entry_order_type=OrderType.LIMIT", source)
        self.assertIn("time_in_force=TimeInForce.FOK", source)
        self.assertIn("CANDIDATE16_V5B_FOK_PRICE_CAP", source)
        self.assertNotIn("entry_trigger_price=", source)
        self.assertIn("preserved_v5_entry_trigger", source)
        self.assertIn("entry_limit_worst_fill", source)

    def test_state_router_is_inherited_unchanged(self) -> None:
        source = (ROOT / "strategy_v5b.py").read_text(encoding="utf-8")
        self.assertIn("from strategy_v5 import Candidate16V5Strategy", source)
        self.assertNotIn("def _detect_sweep", source)
        self.assertNotIn("def _process_pending", source)
        self.assertNotIn("qualify_crowded_shock", source)

    def test_stop_target_cost_and_risk_remain_v5_contracts(self) -> None:
        source = (ROOT / "strategy_v5b.py").read_text(encoding="utf-8")
        self.assertIn("shock_extreme", source)
        self.assertIn("self._natural_target", source)
        self.assertIn("planned_loss_per_unit", source)
        self.assertIn("risk_budget = equity * self.config.risk_fraction", source)
        self.assertIn("planned_account_loss_at_worst_fill", source)

    def test_runner_uses_same_config_and_period(self) -> None:
        source = (ROOT / "candidate_v5b.py").read_text(encoding="utf-8")
        self.assertIn("strategy_v5b:Candidate16V5BStrategy", source)
        self.assertIn("features_v4b", source)
        self.assertIn('"2023-06-05", "2023-06-11"', source)
        self.assertIn("economic_state_changed", source)
        self.assertIn("worst_fill_cap_changed", source)


if __name__ == "__main__":
    unittest.main()
