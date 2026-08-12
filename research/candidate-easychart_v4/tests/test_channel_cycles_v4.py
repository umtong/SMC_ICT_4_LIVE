from __future__ import annotations

import unittest

from domain import Candle, Side
from market_structure import StructurePath
from market_structure_channel_cycles_v4 import (
    CyclicSourceFaithfulMarketStructureDetector,
)
from market_structure_types import (
    BoundaryRole,
    ChannelDirection,
    ChannelState,
    StructuralBoundary,
    StructureKind,
)

NS = 60_000_000_000


def candle(index: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle((index + 1) * NS, o, h, l, c, 1.0)


class ChannelCycleTests(unittest.TestCase):
    def _detector(self) -> tuple[
        CyclicSourceFaithfulMarketStructureDetector,
        ChannelState,
        StructuralBoundary,
        StructuralBoundary,
    ]:
        detector = CyclicSourceFaithfulMarketStructureDetector(
            "TEST",
            1,
            0.1,
            pivot_spans=(10,),
        )
        channel_id = "TEST:1m:CHANNEL:MANUAL"
        lower_id = f"{channel_id}:LOWER"
        upper_id = f"{channel_id}:UPPER"
        mid_id = f"{channel_id}:MID"

        def boundary(
            boundary_id: str,
            kind: StructureKind,
            role: BoundaryRole,
            price: float,
        ) -> StructuralBoundary:
            return StructuralBoundary(
                boundary_id=boundary_id,
                kind=kind,
                role=role,
                timeframe_minutes=1,
                observed_time_ns=0,
                observed_index=-1,
                anchor_1_time_ns=0,
                anchor_1_price=price,
                anchor_2_time_ns=NS,
                anchor_2_price=price,
                strength_ratio=2.0,
                pivot_span=1,
                channel_id=channel_id,
            )

        lower = boundary(
            lower_id,
            StructureKind.CHANNEL_LOWER,
            BoundaryRole.SUPPORT,
            10.0,
        )
        upper = boundary(
            upper_id,
            StructureKind.CHANNEL_UPPER,
            BoundaryRole.RESISTANCE,
            12.0,
        )
        mid = boundary(
            mid_id,
            StructureKind.CHANNEL_MIDLINE,
            BoundaryRole.RESISTANCE,
            11.0,
        )
        lower.opposite_boundary_id = upper_id
        upper.opposite_boundary_id = lower_id
        lower.midline_boundary_id = mid_id
        upper.midline_boundary_id = mid_id
        mid.midline_boundary_id = mid_id
        channel = ChannelState(
            channel_id=channel_id,
            direction=ChannelDirection.ASCENDING,
            timeframe_minutes=1,
            pivot_span=1,
            observed_index=-1,
            observed_time_ns=0,
            lower_boundary_id=lower_id,
            upper_boundary_id=upper_id,
            midline_boundary_id=mid_id,
            anchor_pivot_ids=("P1", "P2", "P3"),
        )
        detector.boundaries.update(
            {lower_id: lower, upper_id: upper, mid_id: mid},
        )
        detector.zones.extend((lower, upper, mid))
        detector.channels[channel_id] = channel
        return detector, channel, lower, upper

    def test_same_edge_retouch_before_opposite_completion_is_not_new_wave(self) -> None:
        detector, _channel, lower, _upper = self._detector()
        first = detector.on_bar(candle(0, 10.5, 11.0, 10.0, 10.4))
        self.assertTrue(
            any(
                event.primary_boundary_id == lower.boundary_id
                and event.side is Side.LONG
                for event in first
            ),
        )
        second = detector.on_bar(candle(1, 10.4, 10.9, 10.0, 10.3))
        self.assertFalse(
            any(event.primary_boundary_id == lower.boundary_id for event in second),
        )
        self.assertTrue(lower.rejection_used)

    def test_opposite_completion_rearms_origin_for_later_independent_wave(self) -> None:
        detector, channel, lower, upper = self._detector()
        first = detector.on_bar(candle(0, 10.5, 11.0, 10.0, 10.4))
        first_lower = [
            event
            for event in first
            if event.primary_boundary_id == lower.boundary_id
            and event.side is Side.LONG
        ]
        self.assertEqual(len(first_lower), 1)

        opposite = detector.on_bar(candle(1, 11.2, 12.0, 10.8, 11.7))
        self.assertTrue(
            any(
                event.primary_boundary_id == upper.boundary_id
                and event.side is Side.SHORT
                for event in opposite
            ),
        )
        self.assertFalse(lower.rejection_used)
        self.assertEqual(
            detector.diagnostics.get(
                "channel_origin_edge_rearmed_after_opposite_completion",
            ),
            1,
        )
        self.assertEqual(channel.last_bounce_boundary_id, upper.boundary_id)

        later = detector.on_bar(candle(2, 10.8, 11.4, 10.0, 10.4))
        later_lower = [
            event
            for event in later
            if event.primary_boundary_id == lower.boundary_id
            and event.side is Side.LONG
        ]
        self.assertEqual(len(later_lower), 1)
        self.assertEqual(
            detector.diagnostics.get(
                "channel_origin_edge_rearmed_after_opposite_completion",
            ),
            2,
        )

    def test_unknown_intrabar_full_span_does_not_rearm_origin_edge(self) -> None:
        detector, _channel, lower, _upper = self._detector()
        detector.on_bar(candle(0, 10.5, 11.0, 10.0, 10.4))
        detector.on_bar(candle(1, 11.0, 12.0, 10.0, 11.0))
        self.assertTrue(lower.rejection_used)
        self.assertEqual(
            detector.diagnostics.get("channel_cycle_full_span_same_bar_unresolved"),
            1,
        )

    def test_accepted_channel_break_deactivates_cycle_instead_of_rearming(self) -> None:
        detector, channel, lower, upper = self._detector()
        detector.on_bar(candle(0, 10.5, 11.0, 10.0, 10.4))
        self.assertEqual(detector.on_bar(candle(1, 10.2, 10.3, 9.5, 9.8)), [])
        resolved = detector.on_bar(candle(2, 9.7, 9.9, 9.3, 9.6))
        self.assertTrue(
            any(
                event.path
                in {
                    StructurePath.ACCEPTANCE,
                    StructurePath.CHANNEL_FAILURE_ACCEPTANCE,
                }
                and event.side is Side.SHORT
                for event in resolved
            ),
        )
        self.assertFalse(channel.active)
        self.assertFalse(lower.active)
        self.assertFalse(upper.active)


if __name__ == "__main__":
    unittest.main()
