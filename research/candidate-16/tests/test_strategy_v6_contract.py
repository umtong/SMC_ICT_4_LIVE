from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Candidate16V6ContractTests(unittest.TestCase):
    def test_state_pullback_and_entry_are_distinct_events(self) -> None:
        source = (ROOT / "strategy_v6.py").read_text(encoding="utf-8")
        router = (ROOT / "informed_initiative_router.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("no_order_on_initiative_bar", source)
        self.assertIn("COUNTER_BAR_HELD_INITIATIVE_MIDPOINT", router)
        self.assertIn("STRICTLY_LATER_PRICE_FLOW_AND_L1", router)
        self.assertIn("initiative bar cannot confirm itself", router)
        self.assertIn("pullback bar cannot confirm itself", router)

    def test_entry_is_all_or_none_price_capped(self) -> None:
        source = (ROOT / "strategy_v6.py").read_text(encoding="utf-8")
        self.assertIn("entry_order_type=OrderType.LIMIT", source)
        self.assertIn("time_in_force=TimeInForce.FOK", source)
        self.assertIn("CANDIDATE16_V6_INFORMED_FOK", source)
        self.assertIn("entry_limit_worst_fill", source)
        self.assertIn("planned_loss_per_unit_at_worst_fill", source)

    def test_pullback_stop_and_natural_target_have_no_fallback(self) -> None:
        source = (ROOT / "strategy_v6.py").read_text(encoding="utf-8")
        self.assertIn("pullback_extreme", source)
        self.assertIn("self.active_pools.values()", source)
        self.assertIn("NO_COST_AWARE_LIQUIDITY_OBJECTIVE", source)
        self.assertNotIn("choose_liquidity_target", source)
        self.assertNotIn("FALLBACK_CAUSAL_EXPANSION", source)

    def test_cost_risk_and_gate_are_frozen(self) -> None:
        config = json.loads((ROOT / "config_v6.json").read_text())
        self.assertEqual(config["risk_fraction"], 0.03)
        self.assertEqual(config["all_in_cost_bps_each_side"], 7.5)
        self.assertEqual(config["adverse_slippage_bps_each_side"], 2.5)
        self.assertEqual(config["gate"]["min_geometric_daily_growth"], 0.01)
        self.assertEqual(config["gate"]["min_trades"], 7)
        self.assertEqual(config["gate"]["min_wins"], 4)
        self.assertEqual(config["gate"]["min_win_rate"], 0.4)

    def test_runner_reuses_fixed_l1_and_nautilus(self) -> None:
        source = (ROOT / "candidate_v6.py").read_text(encoding="utf-8")
        self.assertIn("features_v4b", source)
        self.assertIn("candidate05_backtest.run_backtest", source)
        self.assertIn("strategy_v6:Candidate16V6Strategy", source)
        self.assertIn("NautilusTrader BacktestNode", source)


if __name__ == "__main__":
    unittest.main()
