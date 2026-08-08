from __future__ import annotations

from pathlib import Path
import unittest

import candidate_v7
from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as shared_v4
from strategy_v52_cross_sectional_residual import CrossSectionalResidualStrategy


ROOT = Path(__file__).resolve().parents[1]


class Candidate16V7SharedAdapterTests(unittest.TestCase):
    def test_original_v52_economics_are_inherited_not_copied(self) -> None:
        cls = candidate_v7.Candidate16V7SharedResidualStrategy
        self.assertTrue(issubclass(cls, CrossSectionalResidualStrategy))
        source = (ROOT / "candidate_v7.py").read_text(encoding="utf-8")
        self.assertNotIn("ROBUST_Z=", source)
        self.assertNotIn("def _maybe_arm_cross_sectional", source)
        self.assertIn("CrossSectionalResidualStrategy", source)

    def test_final_shared_coordinator_is_installed(self) -> None:
        self.assertIs(
            shared_v4.SHARED_ACCOUNT_ENTRY_COORDINATOR,
            FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR,
        )

    def test_each_project_symbol_has_an_importable_class(self) -> None:
        for symbol in candidate_v7.PROJECT_SYMBOLS:
            path = candidate_v7.candidate16_v7_strategy_path(
                candidate_v7.V7_WINNER,
                symbol,
            )
            self.assertEqual(
                path,
                f"candidate_v7:Candidate16V7{symbol}Strategy",
            )
            cls = getattr(candidate_v7, f"Candidate16V7{symbol}Strategy")
            self.assertTrue(
                issubclass(
                    cls,
                    candidate_v7.Candidate16V7SharedResidualStrategy,
                ),
            )

    def test_unknown_winner_or_symbol_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            candidate_v7.candidate16_v7_strategy_path("other", "BTCUSDT")
        with self.assertRaises(ValueError):
            candidate_v7.candidate16_v7_strategy_path(
                candidate_v7.V7_WINNER,
                "DOGEUSDT",
            )

    def test_existing_runner_is_patched_only_at_registration_boundary(self) -> None:
        runner = candidate_v7.install_shared_adapter()
        self.assertIs(
            runner._base.final_shared_strategy_path,
            candidate_v7.candidate16_v7_strategy_path,
        )
        source = (ROOT / "candidate_v7.py").read_text(encoding="utf-8")
        self.assertIn("runner._base.run_shared_account", source)
        self.assertNotIn("BacktestNode(", source)
        self.assertNotIn("submit_order", source)
        self.assertNotIn("realized_pnl =", source)


if __name__ == "__main__":
    unittest.main()
