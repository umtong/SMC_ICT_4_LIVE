from __future__ import annotations

from pathlib import Path
import unittest

import candidate_v8
from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as shared_v4
from strategy_v52_cross_sectional_residual import CrossSectionalResidualStrategy
from strategy_v8 import Candidate16V8Strategy
from strategy_v8 import V8_MAX_WAIT_BARS
from strategy_v8 import V8_MIN_TARGET_NET_R


ROOT = Path(__file__).resolve().parents[1]


class Candidate16V8ContractTests(unittest.TestCase):
    def test_original_v52_state_detector_is_inherited(self) -> None:
        self.assertTrue(
            issubclass(Candidate16V8Strategy, CrossSectionalResidualStrategy),
        )
        source = (ROOT / "strategy_v8.py").read_text(encoding="utf-8")
        self.assertIn("super()._maybe_arm_cross_sectional(row)", source)
        self.assertNotIn("ROBUST_Z =", source)
        self.assertNotIn("def _robust_z", source)
        self.assertNotIn("oi > 0.0", source)

    def test_state_bar_cannot_create_an_order(self) -> None:
        source = (ROOT / "strategy_v8.py").read_text(encoding="utf-8")
        freeze = source.index("def _maybe_arm_cross_sectional")
        process = source.index("def _process_pending")
        state_section = source[freeze:process]
        self.assertIn("v8_no_order_on_state_bar", state_section)
        self.assertNotIn("submit_order", state_section)
        self.assertNotIn("_submit_price_capped_bracket", state_section)

    def test_confirmation_uses_strictly_later_independent_roles(self) -> None:
        source = (ROOT / "strategy_v8.py").read_text(encoding="utf-8")
        self.assertIn("if self.bar_index <= setup.created_index", source)
        self.assertIn("abs(residual) < abs(initial)", source)
        self.assertIn("own1 - peer1", source)
        self.assertIn('self._feature("flow_60s")', source)
        self.assertIn('self._feature("depth_imbalance_1")', source)
        self.assertIn("STRICTLY_LATER_RESIDUAL_CONVERGENCE_CONFIRMED", source)
        self.assertIn("same_bar_state_confirmation_reuse", (ROOT / "candidate_v8.py").read_text())

    def test_wait_horizon_matches_positioning_horizon(self) -> None:
        self.assertEqual(V8_MAX_WAIT_BARS, 15)
        source = (ROOT / "strategy_v8.py").read_text(encoding="utf-8")
        self.assertIn("WITHIN_OI_HORIZON", source)

    def test_entry_is_full_or_none_and_worst_fill_risked(self) -> None:
        source = (ROOT / "strategy_v8.py").read_text(encoding="utf-8")
        self.assertIn("entry_order_type=OrderType.LIMIT", source)
        self.assertIn("time_in_force=TimeInForce.FOK", source)
        self.assertIn("CANDIDATE16_V8_FOK_PRICE_CAP", source)
        self.assertIn("planned_loss_per_unit", source)
        self.assertIn("risk_budget = equity * self.config.risk_fraction", source)
        self.assertIn("planned_account_loss_at_worst_fill", source)

    def test_target_is_pre_existing_liquidity_with_no_fallback(self) -> None:
        self.assertEqual(V8_MIN_TARGET_NET_R, 1.0)
        source = (ROOT / "strategy_v8.py").read_text(encoding="utf-8")
        self.assertIn("self.active_pools.values()", source)
        self.assertIn("NO_PRE_EXISTING_LIQUIDITY_TARGET_WITH_ONE_NET_R", source)
        self.assertNotIn("choose_liquidity_target", source)
        self.assertNotIn("fallback_net_r", source)

    def test_shared_account_uses_final_global_slot(self) -> None:
        self.assertIs(
            shared_v4.SHARED_ACCOUNT_ENTRY_COORDINATOR,
            FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR,
        )
        self.assertTrue(
            issubclass(
                candidate_v8.Candidate16V8SharedStrategy,
                Candidate16V8Strategy,
            ),
        )
        for symbol in candidate_v8.PROJECT_SYMBOLS:
            path = candidate_v8.candidate16_v8_strategy_path(
                candidate_v8.V8_WINNER,
                symbol,
            )
            self.assertEqual(
                path,
                f"candidate_v8:Candidate16V8{symbol}Strategy",
            )

    def test_adapter_does_not_implement_engine_or_accounting(self) -> None:
        source = (ROOT / "candidate_v8.py").read_text(encoding="utf-8")
        self.assertIn("runner._base.run_shared_account", source)
        self.assertNotIn("BacktestNode(", source)
        self.assertNotIn("submit_order", source)
        self.assertNotIn("realized_pnl =", source)


if __name__ == "__main__":
    unittest.main()
