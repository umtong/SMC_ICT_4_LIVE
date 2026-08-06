from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from aggtrade_acceptance_probe import (
    acceptance_interaction,
    acceptance_reaccelerates,
    acceptance_retest_holds,
)


def row(**overrides: float) -> pd.Series:
    values = {
        "open": 100.0,
        "high": 101.2,
        "low": 99.9,
        "close": 101.0,
        "volume": 200.0,
        "trade_count": 150.0,
        "imbalance": 0.35,
        "volume_ratio": 1.8,
        "trade_ratio": 1.4,
        "close_location": 0.85,
    }
    values.update(overrides)
    return pd.Series(values)


class AcceptanceOnlyContracts(unittest.TestCase):
    def test_acceptance_requires_close_beyond_boundary_and_outward_flow(self) -> None:
        self.assertTrue(
            acceptance_interaction(row(), boundary_level=100.5, outward=1, atr=1.0)
        )
        self.assertFalse(
            acceptance_interaction(
                row(close=100.52, high=101.2, close_location=0.48),
                boundary_level=100.5,
                outward=1,
                atr=1.0,
            )
        )
        self.assertFalse(
            acceptance_interaction(
                row(imbalance=-0.35),
                boundary_level=100.5,
                outward=1,
                atr=1.0,
            )
        )

    def test_retest_must_hold_boundary_with_lower_energy(self) -> None:
        contracted = row(
            open=100.8,
            high=100.9,
            low=100.52,
            close=100.7,
            volume=100.0,
            trade_count=80.0,
            imbalance=-0.08,
        )
        self.assertTrue(
            acceptance_retest_holds(
                contracted,
                boundary_level=100.5,
                outward=1,
                atr=1.0,
                displacement_volume=200.0,
                displacement_trade_count=150.0,
                displacement_imbalance=0.35,
            )
        )
        self.assertFalse(
            acceptance_retest_holds(
                contracted.copy().set_axis(contracted.index),
                boundary_level=101.0,
                outward=1,
                atr=1.0,
                displacement_volume=200.0,
                displacement_trade_count=150.0,
                displacement_imbalance=0.35,
            )
        )
        hot = contracted.copy()
        hot["volume"] = 190.0
        self.assertFalse(
            acceptance_retest_holds(
                hot,
                boundary_level=100.5,
                outward=1,
                atr=1.0,
                displacement_volume=200.0,
                displacement_trade_count=150.0,
                displacement_imbalance=0.35,
            )
        )

    def test_reacceleration_is_separate_directional_flow_break(self) -> None:
        follow = row(
            open=100.7,
            high=101.4,
            low=100.65,
            close=101.3,
            volume=130.0,
            trade_count=100.0,
            imbalance=0.25,
            close_location=0.87,
        )
        self.assertTrue(
            acceptance_reaccelerates(
                follow,
                outward=1,
                atr=1.0,
                retest_high=100.9,
                retest_low=100.52,
                retest_volume=100.0,
                retest_trade_count=80.0,
            )
        )
        weak = follow.copy()
        weak["imbalance"] = 0.02
        self.assertFalse(
            acceptance_reaccelerates(
                weak,
                outward=1,
                atr=1.0,
                retest_high=100.9,
                retest_low=100.52,
                retest_volume=100.0,
                retest_trade_count=80.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
