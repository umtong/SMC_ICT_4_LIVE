from __future__ import annotations

from pathlib import Path
import sys
import unittest

import candidate_v8
import candidate_v9
from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as shared_v4


ROOT = Path(__file__).resolve().parents[1]
Candidate16V9RoleSeparatedStrategy = candidate_v9.Candidate16V9RoleSeparatedStrategy
_STRATEGY_V9_MODULE = sys.modules[Candidate16V9RoleSeparatedStrategy.__module__]


class Candidate16V9ContractTests(unittest.TestCase):
    def test_v9_changes_state_admission_not_later_transition(self) -> None:
        self.assertTrue(
            issubclass(
                Candidate16V9RoleSeparatedStrategy,
                candidate_v8.Candidate16V8Strategy,
            ),
        )
        source = (ROOT / "strategy_v9_role_separated.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("_robust_z(self.v52_residuals, residual)", source)
        self.assertIn("abs(z) < ROBUST_Z", source)
        self.assertIn('self._feature("oi_change_15m")', source)
        self.assertIn("oi > 0.0", source)
        self.assertNotIn("super()._maybe_arm_cross_sectional(row)", source)
        self.assertNotIn("def _process_pending", source)
        self.assertNotIn("def _submit_v8_entry", source)

    def test_state_bar_microstructure_is_diagnostic_not_gate(self) -> None:
        source = (ROOT / "strategy_v9_role_separated.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("legacy_microstructure_pass = bool", source)
        self.assertIn('"role": "DIAGNOSTIC_ONLY"', source)
        self.assertIn('"state_bar_flow_depth": "DIAGNOSTIC_ONLY"', source)
        self.assertNotIn("if not legacy_microstructure_pass", source)
        self.assertIn("self.pending = setup", source)
        self.assertIn("candidate16_v9_states_frozen", source)

    def test_state_bar_cannot_submit_an_order(self) -> None:
        source = (ROOT / "strategy_v9_role_separated.py").read_text(
            encoding="utf-8",
        )
        method = source[source.index("def _maybe_arm_cross_sectional") :]
        self.assertNotIn("submit_order", method)
        self.assertNotIn("_submit_price_capped_bracket", method)
        self.assertIn("v8_no_order_on_state_bar", method)

    def test_v8_wait_horizon_and_execution_are_reused(self) -> None:
        self.assertEqual(_STRATEGY_V9_MODULE.V8_MAX_WAIT_BARS, 15)
        self.assertEqual(
            candidate_v9.V9_EXECUTION_BRANCH,
            candidate_v8.V8_TRADE_BRANCH,
        )
        source = (ROOT / "candidate_v9.py").read_text(encoding="utf-8")
        self.assertIn('"transition_changed": False', source)
        self.assertIn('"entry_time_in_force": "FOK"', source)
        self.assertIn('"minimum_natural_target_net_r": 1.0', source)

    def test_shared_account_uses_final_global_slot(self) -> None:
        self.assertIs(
            shared_v4.SHARED_ACCOUNT_ENTRY_COORDINATOR,
            FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR,
        )
        for symbol in candidate_v9.PROJECT_SYMBOLS:
            self.assertEqual(
                candidate_v9.candidate16_v9_strategy_path(
                    candidate_v9.V9_WINNER,
                    symbol,
                ),
                f"candidate_v9:Candidate16V9{symbol}Strategy",
            )

    def test_diagnostic_account_is_residual_only(self) -> None:
        source = (ROOT / "candidate_v9.py").read_text(encoding="utf-8")
        self.assertIn("def _detect_position_building_balance", source)
        self.assertIn("NON_RESIDUAL_ENTRY_PATH_ATTEMPTED_IN_V9_ISOLATED_ACCOUNT", source)
        self.assertIn("branch != V9_EXECUTION_BRANCH", source)
        self.assertIn("V9_ROLE_SEPARATED_RESIDUAL_ONLY", source)

    def test_adapter_reuses_engine_and_accounting(self) -> None:
        source = (ROOT / "candidate_v9.py").read_text(encoding="utf-8")
        self.assertIn("runner._base.run_shared_account", source)
        self.assertNotIn("BacktestNode(", source)
        self.assertNotIn("realized_pnl =", source)


if __name__ == "__main__":
    unittest.main()
