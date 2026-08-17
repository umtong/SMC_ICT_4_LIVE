from __future__ import annotations

import math
import unittest

from ml2_model import decision_from_probability, estimate_trade_economics, shadow_decision


class Side:
    def __init__(self, name: str) -> None:
        self.name = name


class ModelEconomicsTest(unittest.TestCase):
    def test_fixed_risk_log_growth_uses_immutable_economics(self) -> None:
        economics = estimate_trade_economics(
            side=Side("LONG"),
            entry=100.0,
            stop=99.0,
            target=101.5,
            tick_size=0.1,
            entry_fee_rate=0.0004,
            target_fee_rate=0.0004,
            stop_fee_rate=0.0004,
            entry_slippage_ticks=1,
            stop_slippage_ticks=1,
        )
        low = decision_from_probability(0.35, economics, risk_fraction=0.03)
        high = decision_from_probability(0.80, economics, risk_fraction=0.03)
        self.assertFalse(low.accepted)
        self.assertTrue(high.accepted)
        self.assertGreater(high.expected_log_growth, 0.0)
        self.assertTrue(0.0 < high.required_probability < 1.0)
        self.assertNotAlmostEqual(
            high.required_probability,
            high.arithmetic_break_even_probability,
        )
        shadow = shadow_decision(economics, risk_fraction=0.03)
        self.assertFalse(shadow.accepted)
        self.assertTrue(math.isnan(shadow.target_probability))

    def test_short_cost_geometry(self) -> None:
        economics = estimate_trade_economics(
            side=Side("SHORT"),
            entry=100.0,
            stop=101.0,
            target=98.0,
            tick_size=0.1,
            entry_fee_rate=0.0004,
            target_fee_rate=0.0002,
            stop_fee_rate=0.0004,
            entry_slippage_ticks=1,
            stop_slippage_ticks=1,
        )
        self.assertGreater(economics.win_net_r, 1.0)
        self.assertLess(economics.loss_net_r, -1.0)


if __name__ == "__main__":
    unittest.main()
