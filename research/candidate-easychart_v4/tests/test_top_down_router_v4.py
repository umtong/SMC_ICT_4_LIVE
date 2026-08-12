from __future__ import annotations

import unittest

from domain import Candle, Side
from scenario_runtime_v4 import ResearchScenarioBundleV4

NS = 60_000_000_000


def bar(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle((i + 1) * 60 * NS, o, h, l, c, 1.0)


def ascending_seed() -> list[Candle]:
    return [
        bar(0, 12.0, 13.0, 11.8, 12.5),
        bar(1, 11.4, 12.2, 10.0, 11.5),
        bar(2, 13.0, 16.0, 12.0, 15.0),
        bar(3, 12.0, 14.0, 11.0, 13.0),
        bar(4, 13.5, 15.0, 12.5, 14.0),
    ]


class TopDownRouterV4Tests(unittest.TestCase):
    def _bundle_with_channel(self) -> ResearchScenarioBundleV4:
        bundle = ResearchScenarioBundleV4("TEST", 0.1)
        bundle.macro.structure.pivot_spans = (1,)
        for candle in ascending_seed():
            bundle.on_bar(60, candle)
        return bundle

    def test_unresolved_higher_event_context_rejects_micro_trade(self) -> None:
        bundle = ResearchScenarioBundleV4("TEST", 0.1)
        allowed, side, basis = bundle._micro_permission(Side.LONG)
        self.assertFalse(allowed)
        self.assertIsNone(side)
        self.assertEqual(basis, "UNRESOLVED_1H_EVENT_CONTEXT")

    def test_channel_direction_without_interaction_does_not_route_micro(self) -> None:
        bundle = self._bundle_with_channel()
        self.assertEqual(len(bundle.macro.structure.channels), 1)
        side, basis = bundle._higher_context_side()
        self.assertIsNone(side)
        self.assertEqual(basis, "UNRESOLVED_1H_EVENT_CONTEXT")

    def test_confirmed_1h_bounce_activates_event_side_not_old_channel_slope(self) -> None:
        bundle = self._bundle_with_channel()
        # The first causally later lower-edge interaction is the fourth point.
        bundle.on_bar(60, bar(5, 13.0, 14.0, 12.0, 13.2))
        side, basis = bundle._higher_context_side()
        self.assertIs(side, Side.LONG)
        self.assertTrue(basis.startswith("LIVE_1H_EVENT:BOUNCE:CHANNEL_LOWER:"))
        self.assertTrue(bundle._micro_permission(Side.LONG)[0])
        self.assertFalse(bundle._micro_permission(Side.SHORT)[0])

    def test_context_clears_at_its_own_structural_stop(self) -> None:
        bundle = self._bundle_with_channel()
        bundle.on_bar(60, bar(5, 13.0, 14.0, 12.0, 13.2))
        _side, _basis, event, confirmed_time = bundle.macro.context_state()
        self.assertIsNotNone(event)
        assert event is not None and confirmed_time is not None
        stop_bar = Candle(
            ts_close_ns=confirmed_time + 60 * NS,
            open=event.reference_close,
            high=event.reference_close + 0.2,
            low=event.stop_reference - 0.1,
            close=event.stop_reference,
            volume=1.0,
        )
        bundle.macro._update_context_lifecycle(stop_bar)
        side, basis = bundle._higher_context_side()
        self.assertIsNone(side)
        self.assertEqual(basis, "UNRESOLVED_1H_EVENT_CONTEXT")

    def test_context_clears_at_its_own_structural_objective(self) -> None:
        bundle = self._bundle_with_channel()
        bundle.on_bar(60, bar(5, 13.0, 14.0, 12.0, 13.2))
        _side, _basis, event, confirmed_time = bundle.macro.context_state()
        self.assertIsNotNone(event)
        assert event is not None and confirmed_time is not None
        target = bundle.macro._context_target(event, confirmed_time + 60 * NS)
        self.assertIsNotNone(target)
        assert target is not None
        target_bar = Candle(
            ts_close_ns=confirmed_time + 60 * NS,
            open=event.reference_close,
            high=target + 0.1,
            low=event.reference_close - 0.1,
            close=target,
            volume=1.0,
        )
        bundle.macro._update_context_lifecycle(target_bar)
        side, basis = bundle._higher_context_side()
        self.assertIsNone(side)
        self.assertEqual(basis, "UNRESOLVED_1H_EVENT_CONTEXT")


if __name__ == "__main__":
    unittest.main()
