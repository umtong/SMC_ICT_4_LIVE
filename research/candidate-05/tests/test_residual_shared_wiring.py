from __future__ import annotations

import inspect
from pathlib import Path
import unittest

import residual_shared_account_backtest as runner
import residual_shared_strategy_variants as variants
import strategy_global_slot_wrappers_v4 as shared
from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
from strategy_v52_cross_sectional_residual import CrossSectionalResidualStrategy
from strategy_v53_residual_competition import ResidualStateCompetitionStrategy


class ResidualSharedWiringTest(unittest.TestCase):
    def test_v36_source_is_preserved(self) -> None:
        path = Path(__file__).resolve().parents[1] / "strategy_v36_cross_asset_repricing_gate.py"
        source = path.read_text(encoding="utf-8")
        self.assertIn("class SystemicRepricingGateMixin", source)
        self.assertNotEqual(source.strip(), "from strategy_v52_cross_sectional_residual import CrossSectionalResidualStrategy")

    def test_final_coordinator_is_injected_into_audited_lifecycle(self) -> None:
        self.assertIs(shared.SHARED_ACCOUNT_ENTRY_COORDINATOR, FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR)

    def test_lifecycle_precedes_market_logic_in_mro(self) -> None:
        v52 = variants.FinalSharedAccountV52Strategy.mro()
        v53 = variants.FinalSharedAccountV53Strategy.mro()
        self.assertLess(v52.index(shared.SharedAccountEntryLifecycleMixin), v52.index(CrossSectionalResidualStrategy))
        self.assertLess(v53.index(shared.SharedAccountEntryLifecycleMixin), v53.index(ResidualStateCompetitionStrategy))

    def test_all_four_paths_resolve_for_both_fixed_hypotheses(self) -> None:
        for winner, family in variants.WINNER_TO_FAMILY.items():
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
                path = variants.residual_shared_strategy_path(winner, symbol)
                klass = variants.residual_shared_strategy_class(winner, symbol)
                self.assertIn(f"FinalResidual{family.upper()}{symbol}Strategy", path)
                self.assertEqual(klass.__module__, variants.__name__)

    def test_runner_does_not_write_or_replace_strategy_sources(self) -> None:
        source = inspect.getsource(runner)
        self.assertNotIn("write_text", source)
        self.assertNotIn("strategy_v36_cross_asset_repricing_gate.py", source)
        self.assertIs(runner._base.final_shared_strategy_path, variants.residual_shared_strategy_path)

    def test_hypothesis_manifest_loader_does_not_claim_validated_winner(self) -> None:
        source = inspect.getsource(runner.load_pre_registered_family)
        self.assertIn("PRE_REGISTERED_FOUR_ASSET_MECHANISM", source)
        self.assertNotIn("VALIDATED_BTC_WINNER_RESOLVED", source)


if __name__ == "__main__":
    unittest.main()
