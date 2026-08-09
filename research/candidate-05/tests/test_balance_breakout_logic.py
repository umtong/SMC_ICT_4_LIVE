from __future__ import annotations

from dataclasses import replace
import unittest

from balance_breakout_logic import BALANCE_BARS
from balance_breakout_logic import BalanceBreakoutEvidence
from balance_breakout_logic import breakout_side


def evidence(side: int) -> BalanceBreakoutEvidence:
    closes = tuple(100.0 + (0.1 if index % 2 else -0.1) for index in range(BALANCE_BARS))
    if side > 0:
        return BalanceBreakoutEvidence(
            open_price=100.2,
            high=101.4,
            low=100.1,
            close=101.3,
            atr=1.0,
            balance_high=101.0,
            balance_low=99.0,
            balance_closes=closes,
            flow_15s=0.20,
            flow_60s=0.25,
            flow_3m=0.10,
            efficiency_60s=0.25,
            notional_burst=1.10,
            depth_imbalance_1=0.10,
            bid_depth_change_5m=0.02,
            ask_depth_change_5m=-0.02,
            oi_change_15m=0.001,
            metrics_ready=True,
        )
    return BalanceBreakoutEvidence(
        open_price=99.8,
        high=99.9,
        low=98.6,
        close=98.7,
        atr=1.0,
        balance_high=101.0,
        balance_low=99.0,
        balance_closes=closes,
        flow_15s=-0.20,
        flow_60s=-0.25,
        flow_3m=-0.10,
        efficiency_60s=0.25,
        notional_burst=1.10,
        depth_imbalance_1=-0.10,
        bid_depth_change_5m=-0.02,
        ask_depth_change_5m=0.02,
        oi_change_15m=0.001,
        metrics_ready=True,
    )


class BalanceBreakoutLogicTest(unittest.TestCase):
    def test_long_and_short_are_mirror_symmetric(self) -> None:
        self.assertEqual(breakout_side(evidence(1)), 1)
        self.assertEqual(breakout_side(evidence(-1)), -1)

    def test_expansion_requires_position_and_liquidity_sponsorship(self) -> None:
        base = evidence(1)
        self.assertEqual(breakout_side(base), 1)
        self.assertEqual(breakout_side(replace(base, oi_change_15m=-0.001)), 0)
        self.assertEqual(breakout_side(replace(base, ask_depth_change_5m=0.02)), 0)

    def test_expansion_rejects_opposing_book_or_missing_metrics(self) -> None:
        base = evidence(1)
        self.assertEqual(breakout_side(replace(base, depth_imbalance_1=-0.10)), 0)
        self.assertEqual(breakout_side(replace(base, metrics_ready=False)), 0)


if __name__ == "__main__":
    unittest.main()
