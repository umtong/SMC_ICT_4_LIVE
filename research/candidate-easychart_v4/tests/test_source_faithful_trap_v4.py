from __future__ import annotations

import unittest

from domain import Candle, Side
from market_structure import StructurePath
from market_structure_trap_v4 import SourceFaithfulMarketStructureDetector

NS = 60_000_000_000


def bar(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle((i + 1) * NS, o, h, l, c, 1.0)


def ascending_seed() -> list[Candle]:
    return [
        bar(0, 12.0, 13.0, 11.8, 12.5),
        bar(1, 11.4, 12.2, 10.0, 11.5),
        bar(2, 13.0, 16.0, 12.0, 15.0),
        bar(3, 12.0, 14.0, 11.0, 13.0),
        bar(4, 13.5, 15.0, 12.5, 14.0),
    ]


class SourceFaithfulTrapTests(unittest.TestCase):
    def _detector(self) -> SourceFaithfulMarketStructureDetector:
        detector = SourceFaithfulMarketStructureDetector(
            "TEST",
            1,
            0.1,
            pivot_spans=(1,),
        )
        for candle in ascending_seed():
            detector.on_bar(candle)
        return detector

    def _accepted_short(
        self,
    ) -> tuple[SourceFaithfulMarketStructureDetector, object]:
        detector = self._detector()
        self.assertEqual(detector.on_bar(bar(5, 13.0, 13.2, 11.0, 11.5)), [])
        events = detector.on_bar(bar(6, 11.6, 12.0, 10.8, 11.7))
        accepted = [
            event
            for event in events
            if event.side is Side.SHORT
            and event.path
            in {
                StructurePath.ACCEPTANCE,
                StructurePath.CHANNEL_FAILURE_ACCEPTANCE,
            }
        ]
        self.assertTrue(accepted)
        self.assertTrue(detector._accepted_trap_episodes)
        return detector, accepted[0]

    def test_immediate_next_bar_reentry_is_fakeout_not_trap(self) -> None:
        detector = self._detector()
        self.assertEqual(detector.on_bar(bar(5, 13.0, 13.2, 11.0, 11.5)), [])
        events = detector.on_bar(bar(6, 11.6, 13.5, 10.8, 13.0))
        self.assertTrue(
            any(
                event.path is StructurePath.FAKEOUT and event.side is Side.LONG
                for event in events
            ),
        )
        self.assertFalse(
            any(event.path is StructurePath.TRAP_REENTRY for event in events),
        )
        self.assertEqual(
            detector.diagnostics.get(
                "immediate_break_reentry_reclassified_as_fakeout",
            ),
            1,
        )

    def test_first_reentry_without_confirmed_outside_pivot_is_not_trap(self) -> None:
        detector, _accepted = self._accepted_short()
        events = detector.on_bar(bar(7, 12.0, 14.0, 10.4, 13.4))
        self.assertFalse(
            any(event.path is StructurePath.TRAP_REENTRY for event in events),
        )
        self.assertFalse(detector._accepted_trap_episodes)
        self.assertEqual(
            detector.diagnostics.get(
                "accepted_break_first_reentry_without_confirmed_outside_pivot",
            ),
            1,
        )

    def test_same_bar_pivot_confirmation_cannot_authorize_trap(self) -> None:
        detector, _accepted = self._accepted_short()
        detector.on_bar(bar(7, 11.0, 12.0, 10.2, 11.0))
        # This bar confirms index 7 as a pivot, but the pivot was not available
        # before this same close resolved the return inside.
        events = detector.on_bar(bar(8, 11.2, 14.0, 10.4, 13.8))
        self.assertFalse(
            any(event.path is StructurePath.TRAP_REENTRY for event in events),
        )
        self.assertFalse(detector._accepted_trap_episodes)

    def test_confirmed_second_outside_low_then_later_reentry_is_trap(self) -> None:
        detector, _accepted = self._accepted_short()
        detector.on_bar(bar(7, 11.0, 12.0, 10.2, 11.0))
        detector.on_bar(bar(8, 11.1, 12.2, 10.4, 11.5))
        events = detector.on_bar(bar(9, 13.0, 14.5, 10.5, 14.2))
        traps = [
            event
            for event in events
            if event.path is StructurePath.TRAP_REENTRY
            and event.side is Side.LONG
        ]
        self.assertEqual(len(traps), 1)
        self.assertLess(traps[0].stop_reference, 10.2)
        self.assertFalse(detector._accepted_trap_episodes)
        self.assertEqual(
            detector.diagnostics.get("accepted_break_source_trap_confirmed"),
            1,
        )

    def test_acceptance_objective_consumes_episode_before_late_reversal(self) -> None:
        detector, accepted = self._accepted_short()
        self.assertIsNotNone(accepted.target_boundary_id)
        target = detector.find_boundary(accepted.target_boundary_id or "")
        self.assertIsNotNone(target)
        assert target is not None
        target_level = target.level_at(bar(7, 0, 0, 0, 0).ts_close_ns)
        detector.on_bar(
            bar(
                7,
                target_level + 0.4,
                target_level + 0.6,
                target_level - 0.1,
                target_level + 0.2,
            ),
        )
        self.assertFalse(detector._accepted_trap_episodes)
        self.assertEqual(
            detector.diagnostics.get("accepted_break_target_reached_before_trap"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
