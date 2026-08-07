from __future__ import annotations

from pathlib import Path
import unittest

from portfolio_materializer import materialize_combined_portfolio_source
from runner_materializer import materialize_runner_source


ROOT = Path(__file__).resolve().parent


class CombinedPortfolioMaterializerTests(unittest.TestCase):
    def materialized(self) -> str:
        source = (ROOT / "run_leadership_scdam_base.py").read_text(encoding="utf-8")
        source = materialize_runner_source(source)
        return materialize_combined_portfolio_source(source)

    def test_materialization_is_exact_and_compilable(self):
        source = self.materialized()
        self.assertEqual(source.count("candidate-14-unified-parent"), 1)
        self.assertEqual(source.count("Candidate 12 I7 observes only BTC"), 1)
        self.assertEqual(source.count("SessionAuctionBridge("), 1)
        compile(source, str(ROOT / "run_leadership_scdam_base.py"), "exec")

    def test_one_global_mutex_remains_the_only_portfolio_slot(self):
        source = self.materialized()
        self.assertEqual(source.count("self.mutex = GlobalCandidateMutex()"), 1)
        self.assertEqual(source.count("self.mutex.add(candidate)"), 1)
        self.assertEqual(source.count("self.mutex.flush()"), 1)
        self.assertIn("plans.append((session_plan, session_candidate))", source)

    def test_limit_parent_uses_plan_specific_post_only_semantics(self):
        source = self.materialized()
        self.assertIn("entry_post_only=bool(plan.entry_post_only)", source)
        self.assertNotIn("entry_post_only=True,\n                        tp_order_type", source)

    def test_session_and_core_share_common_nav_sizer_and_submit(self):
        source = self.materialized()
        self.assertEqual(source.count("self.sizer = RiskSizer(logic_config.risk_fraction)"), 1)
        self.assertEqual(source.count("def _submit(self, plan: TradePlan, candidate: Candidate)"), 1)
        self.assertIn("self._submit(winner[0], winner[1])", source)
        self.assertIn('"module": str(plan.details.get("module", "SCDAM_CORE"))', source)


if __name__ == "__main__":
    unittest.main()
