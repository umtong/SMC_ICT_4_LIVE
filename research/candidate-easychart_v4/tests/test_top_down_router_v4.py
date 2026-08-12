from __future__ import annotations

import unittest

from domain import Candle, Side
from scenario_runtime_v4 import ResearchScenarioBundleV4

NS = 60_000_000_000


def bar(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle((i + 1) * 60 * NS, o, h, l, c, 1.0)


class TopDownRouterV4Tests(unittest.TestCase):
    def test_unresolved_higher_context_rejects_micro_trade(self) -> None:
        bundle = ResearchScenarioBundleV4("TEST", 0.1)
        allowed, side, basis = bundle._micro_permission(Side.LONG)
        self.assertFalse(allowed)
        self.assertIsNone(side)
        self.assertEqual(basis, "UNRESOLVED_1H_CONTEXT")

    def test_largest_active_ascending_1h_channel_routes_only_longs(self) -> None:
        bundle = ResearchScenarioBundleV4("TEST", 0.1)
        bundle.macro.structure.pivot_spans = (1,)
        seed = [
            bar(0, 12.0, 13.0, 11.8, 12.5),
            bar(1, 11.4, 12.2, 10.0, 11.5),
            bar(2, 13.0, 16.0, 12.0, 15.0),
            bar(3, 12.0, 14.0, 11.0, 13.0),
            bar(4, 13.5, 15.0, 12.5, 14.0),
        ]
        for candle in seed:
            bundle.on_bar(60, candle)
        side, basis = bundle._higher_context_side()
        self.assertIs(side, Side.LONG)
        self.assertTrue(basis.startswith("ACTIVE_1H_CHANNEL:"))
        self.assertTrue(bundle._micro_permission(Side.LONG)[0])
        self.assertFalse(bundle._micro_permission(Side.SHORT)[0])

    def test_largest_active_descending_1h_channel_routes_only_shorts(self) -> None:
        bundle = ResearchScenarioBundleV4("TEST", 0.1)
        bundle.macro.structure.pivot_spans = (1,)
        seed = [
            bar(0, 14.0, 14.2, 13.0, 13.5),
            bar(1, 14.5, 16.0, 13.8, 14.5),
            bar(2, 12.0, 14.0, 10.0, 11.0),
            bar(3, 13.0, 15.0, 11.0, 12.0),
            bar(4, 11.5, 12.5, 10.5, 11.0),
        ]
        for candle in seed:
            bundle.on_bar(60, candle)
        side, basis = bundle._higher_context_side()
        self.assertIs(side, Side.SHORT)
        self.assertTrue(basis.startswith("ACTIVE_1H_CHANNEL:"))
        self.assertTrue(bundle._micro_permission(Side.SHORT)[0])
        self.assertFalse(bundle._micro_permission(Side.LONG)[0])


if __name__ == "__main__":
    unittest.main()
