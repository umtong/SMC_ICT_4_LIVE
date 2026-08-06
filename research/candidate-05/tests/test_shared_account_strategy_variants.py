from __future__ import annotations

import unittest

from shared_account_strategy_variants import WINNER_TO_FAMILY
from shared_account_strategy_variants import shared_strategy_class
from shared_account_strategy_variants import shared_strategy_class_name
from shared_account_strategy_variants import shared_strategy_path


class SharedAccountStrategyVariantsTest(unittest.TestCase):
    def test_every_validated_winner_has_four_distinct_importable_classes(self) -> None:
        for winner in WINNER_TO_FAMILY:
            names = {
                shared_strategy_class_name(winner, symbol)
                for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
            }
            self.assertEqual(len(names), 4)
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
                cls = shared_strategy_class(winner, symbol)
                self.assertEqual(cls.__name__, shared_strategy_class_name(winner, symbol))
                self.assertEqual(
                    shared_strategy_path(winner, symbol),
                    f"shared_account_strategy_variants:{cls.__name__}",
                )

    def test_unknown_winner_or_symbol_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            shared_strategy_class_name("unknown:Strategy", "BTCUSDT")
        winner = next(iter(WINNER_TO_FAMILY))
        with self.assertRaises(ValueError):
            shared_strategy_class_name(winner, "DOGEUSDT")


if __name__ == "__main__":
    unittest.main()
