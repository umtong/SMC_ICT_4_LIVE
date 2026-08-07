from __future__ import annotations

import inspect
import unittest

from balanced_auction_nautilus_runner import run_balanced_auction_nautilus_backtest


class RunnerContractTests(unittest.TestCase):
    def test_runner_is_nautilus_only_and_injects_before_run(self):
        source = inspect.getsource(run_balanced_auction_nautilus_backtest)
        self.assertIn("BacktestEngine", source)
        self.assertIn("engine.add_instrument", source)
        self.assertIn("engine.add_data", source)
        self.assertIn("engine.add_strategy", source)
        self.assertIn("BalancedAuctionValueReversionEngine", source)
        self.assertIn('base_logic["engine"] = "LIQUIDITY_RESPONSE_BIFURCATION"', source)
        self.assertLess(source.index("BalancedAuctionValueReversionEngine"), source.index("engine.add_strategy"))


if __name__ == "__main__":
    unittest.main()
