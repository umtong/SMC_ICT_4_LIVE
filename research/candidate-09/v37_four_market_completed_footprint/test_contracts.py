import unittest
from pathlib import Path

from features import _longest_run, _symbol_and_tick
from portfolio_strategy import STRATEGY_PATHS


class Candidate37Contracts(unittest.TestCase):
    def test_project_symbol_tick_sizes(self):
        expected = {
            "BTCUSDT": 0.1,
            "ETHUSDT": 0.01,
            "SOLUSDT": 0.001,
            "XRPUSDT": 0.0001,
        }
        for symbol, tick in expected.items():
            found_symbol, found_tick = _symbol_and_tick(
                Path(f"{symbol}-aggTrades-2024-01-01.zip")
            )
            self.assertEqual(found_symbol, symbol)
            self.assertEqual(found_tick, tick)

    def test_stack_price_uses_symbol_tick(self):
        length, low, high = _longest_run(
            ticks=[1000, 1001, 1002],
            flags=[True, True, True],
            tick_size=0.01,
        )
        self.assertEqual(length, 3)
        self.assertAlmostEqual(low, 10.00)
        self.assertAlmostEqual(high, 10.02)

    def test_unique_strategy_class_per_symbol(self):
        self.assertEqual(set(STRATEGY_PATHS), {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"})
        self.assertEqual(len(set(STRATEGY_PATHS.values())), 4)


if __name__ == "__main__":
    unittest.main()
