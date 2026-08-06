from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from aggtrade_acceptance_no_contraction_ablation import (
    retest_holds_without_contraction,
)
from aggtrade_acceptance_probe import acceptance_retest_holds


class NoContractionAblationTests(unittest.TestCase):
    def test_only_contraction_is_made_non_binding(self) -> None:
        row = pd.Series(
            {
                "open": 100.8,
                "high": 101.0,
                "low": 99.95,
                "close": 100.6,
                "volume": 500.0,
                "trade_count": 500.0,
                "imbalance": 0.8,
                "close_location": 0.62,
            }
        )
        frozen = acceptance_retest_holds(
            row,
            boundary_level=100.0,
            outward=1,
            atr=1.0,
            displacement_volume=100.0,
            displacement_trade_count=100.0,
            displacement_imbalance=0.4,
        )
        ablated = retest_holds_without_contraction(
            row,
            boundary_level=100.0,
            outward=1,
            atr=1.0,
            displacement_volume=100.0,
            displacement_trade_count=100.0,
            displacement_imbalance=0.4,
        )
        self.assertFalse(frozen)
        self.assertTrue(ablated)

    def test_structural_hold_still_required(self) -> None:
        row = pd.Series(
            {
                "open": 101.5,
                "high": 102.0,
                "low": 101.2,
                "close": 101.7,
                "volume": 10.0,
                "trade_count": 10.0,
                "imbalance": 0.0,
                "close_location": 0.625,
            }
        )
        self.assertFalse(
            retest_holds_without_contraction(
                row,
                boundary_level=100.0,
                outward=1,
                atr=1.0,
                displacement_volume=100.0,
                displacement_trade_count=100.0,
                displacement_imbalance=0.4,
            )
        )


if __name__ == "__main__":
    unittest.main()
