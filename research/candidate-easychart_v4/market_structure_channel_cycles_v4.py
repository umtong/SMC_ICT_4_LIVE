"""Re-arm a channel edge only after a complete opposite-edge traversal.

EasyChart describes a channel as a sequence of waves between two parallel
boundaries. The first trade is the fourth point, but a later alternating edge
interaction is a new wave rather than a duplicate touch. The baseline detector
marked an edge permanently used after its first rejection, which discarded all
later completed channel cycles.

This overlay keeps the strict one-touch rule inside a wave. The origin edge is
eligible again only after price has reached the opposite channel boundary. A
full traversal is therefore the causal reset; no bar-count cooldown, ATR gate or
optimized threshold is introduced.
"""
from __future__ import annotations

from domain import Candle
from market_structure_trap_v4 import SourceFaithfulMarketStructureDetector
from market_structure_types import BoundaryRole


class CyclicSourceFaithfulMarketStructureDetector(
    SourceFaithfulMarketStructureDetector,
):
    """Allow alternating independent channel waves, never repeated edge taps."""

    SOURCE_RULES = SourceFaithfulMarketStructureDetector.SOURCE_RULES + (
        "SOURCE_EXPLICIT:CHANNEL_PRICE_MOVES_AS_WAVES_BETWEEN_PARALLEL_BOUNDARIES",
        "SOURCE_EXPLICIT:CHANNEL_REJECTION_OBJECTIVE_IS_THE_OPPOSITE_BOUNDARY",
    )
    TRANSLATION_RULES = SourceFaithfulMarketStructureDetector.TRANSLATION_RULES + (
        "HUMAN_NATURAL_INFERENCE:OPPOSITE_BOUNDARY_COMPLETION_REARMS_THE_ORIGIN_EDGE_FOR_A_NEW_WAVE",
        "HUMAN_NATURAL_INFERENCE:RETURNING_TO_THE_SAME_EDGE_BEFORE_OPPOSITE_COMPLETION_IS_THE_SAME_CAUSAL_EPISODE",
    )

    def _update_channel_midlines(self, bar: Candle) -> None:
        super()._update_channel_midlines(bar)
        for channel in self.channels.values():
            if not channel.active or channel.last_bounce_boundary_id is None:
                continue
            if channel.last_bounce_time_ns is None or bar.ts_close_ns <= channel.last_bounce_time_ns:
                continue
            origin = self.boundaries.get(channel.last_bounce_boundary_id)
            if origin is None or origin.opposite_boundary_id is None:
                continue
            opposite = self.boundaries.get(origin.opposite_boundary_id)
            if opposite is None or not opposite.active:
                continue

            lower = self.boundaries[channel.lower_boundary_id]
            upper = self.boundaries[channel.upper_boundary_id]
            lower_level = lower.level_at(bar.ts_close_ns)
            upper_level = upper.level_at(bar.ts_close_ns)
            if bar.low <= lower_level and bar.high >= upper_level:
                # Intrabar ordering is unavailable; do not create a reset from
                # a bar which traversed both edges in an unknown order.
                self._inc("channel_cycle_full_span_same_bar_unresolved")
                continue

            opposite_level = opposite.level_at(bar.ts_close_ns)
            reached = (
                bar.high >= opposite_level
                if origin.role is BoundaryRole.SUPPORT
                else bar.low <= opposite_level
            )
            if not reached:
                continue

            origin.rejection_used = False
            channel.last_bounce_boundary_id = None
            channel.last_bounce_time_ns = None
            channel.midline_reached_after_bounce = False
            self._inc("channel_origin_edge_rearmed_after_opposite_completion")


__all__ = ["CyclicSourceFaithfulMarketStructureDetector"]
