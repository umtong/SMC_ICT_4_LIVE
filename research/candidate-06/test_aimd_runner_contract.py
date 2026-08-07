from __future__ import annotations

import inspect
from pathlib import Path
import unittest

from auction_imbalance_migration_engine import AuctionImbalanceMigrationDiscoveryEngine


class RunnerContractTests(unittest.TestCase):
    def test_prior_only_profile_order(self):
        source = inspect.getsource(AuctionImbalanceMigrationDiscoveryEngine.observe)
        self.assertLess(source.index("_maybe_start_retest"), source.index("_ingest_completed_profile"))

    def test_nautilus_remains_execution_and_accounting_engine(self):
        source = Path(__file__).with_name("auction_migration_nautilus_runner.py").read_text(encoding="utf-8")
        for token in (
            "BacktestEngine",
            "engine.add_instrument",
            "engine.add_data",
            "engine.add_strategy",
            "engine.run()",
            "OtoTriggerMode.PARTIAL",
        ):
            self.assertIn(token, source)
        self.assertIn('base_logic["engine"] = "LIQUIDITY_RESPONSE_BIFURCATION"', source)
        self.assertIn("AuctionImbalanceMigrationDiscoveryEngine", source)


if __name__ == "__main__":
    unittest.main()
