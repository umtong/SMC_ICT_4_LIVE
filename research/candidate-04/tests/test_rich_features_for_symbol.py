from __future__ import annotations

import unittest

import rich_features_for_symbol as candidate


class SourceTransformationTests(unittest.TestCase):
    def test_only_btc_literal_is_replaced_for_allowed_symbol(self) -> None:
        source = 'A = "BTCUSDT"\nB = \'BTCUSDT\'\nC = "BTC"\n'
        result = candidate.transform_source(source, "ETHUSDT")
        self.assertIn('A = "ETHUSDT"', result)
        self.assertIn("B = 'ETHUSDT'", result)
        self.assertIn('C = "BTC"', result)
        self.assertNotIn("BTCUSDT", result)

    def test_disallowed_symbol_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            candidate.transform_source('SYMBOL = "BTCUSDT"', "DOGEUSDT")


if __name__ == "__main__":
    unittest.main()
