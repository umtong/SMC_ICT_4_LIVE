from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Candidate16V5ContractTests(unittest.TestCase):
    def test_state_and_confirmation_are_strictly_separate(self) -> None:
        source = (ROOT / "strategy_v5.py").read_text(encoding="utf-8")
        router = (ROOT / "crowded_initiative_router.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("no_order_on_shock_bar", source)
        self.assertIn("LaterFailureObservation", source)
        self.assertIn("shock bar cannot confirm itself", router)
        self.assertIn("STRICTLY_LATER_PRICE_FLOW_AND_L1_PRESSURE", router)

    def test_entry_is_price_capped_and_risked_at_worst_fill(self) -> None:
        source = (ROOT / "strategy_v5.py").read_text(encoding="utf-8")
        self.assertIn("entry_order_type=OrderType.STOP_LIMIT", source)
        self.assertIn("entry_limit_worst_fill", source)
        self.assertIn("planned_loss_per_unit_at_worst_fill", source)
        self.assertIn("risk_budget = equity * self.config.risk_fraction", source)
        self.assertIn("time_in_force=TimeInForce.GTC", source)

    def test_target_is_natural_and_has_no_fallback(self) -> None:
        source = (ROOT / "strategy_v5.py").read_text(encoding="utf-8")
        self.assertIn("CROWDED_INITIATIVE_ORIGIN", source)
        self.assertIn("self.active_pools.values()", source)
        self.assertIn("NO_COST_AWARE_NATURAL_OBJECTIVE", source)
        self.assertNotIn("FALLBACK_CAUSAL_EXPANSION", source)
        self.assertNotIn("choose_liquidity_target", source)

    def test_runner_uses_repaired_l1_and_nautilus(self) -> None:
        source = (ROOT / "candidate_v5.py").read_text(encoding="utf-8")
        self.assertIn("features_v4b", source)
        self.assertIn("candidate05_backtest.run_backtest", source)
        self.assertIn("strategy_v5:Candidate16V5Strategy", source)
        self.assertIn("NautilusTrader BacktestNode", source)

    def test_cost_risk_and_gate_are_unchanged(self) -> None:
        config = json.loads((ROOT / "config_v5.json").read_text())
        self.assertEqual(config["risk_fraction"], 0.03)
        self.assertEqual(config["all_in_cost_bps_each_side"], 7.5)
        self.assertEqual(config["adverse_slippage_bps_each_side"], 2.5)
        self.assertEqual(config["gate"]["min_geometric_daily_growth"], 0.01)
        self.assertEqual(config["gate"]["min_trades"], 7)
        self.assertEqual(config["gate"]["min_wins"], 4)
        self.assertEqual(config["gate"]["min_win_rate"], 0.4)

    def test_full_friction_is_the_shock_floor(self) -> None:
        source = (ROOT / "strategy_v5.py").read_text(encoding="utf-8")
        self.assertIn("2.0 * (", source)
        self.assertIn("all_in_cost_bps_each_side", source)
        self.assertIn("adverse_slippage_bps_each_side", source)


if __name__ == "__main__":
    unittest.main()
