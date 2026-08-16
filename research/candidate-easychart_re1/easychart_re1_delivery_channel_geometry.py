"""Structural invalidation for accepted channel breaks.

A channel is a moving boundary, not the complete breakout thesis.  Using one
tick beyond the projected edge as an intrabar stop converts ordinary retest
noise into a full loss and manufactures very large planned R from an unrealistically
small denominator.  The supplied trend/channel material places accepted-break
invalidation at the wave which created the break or at a confirmed decision-frame
counter swing.

For channel acceptance the initial stop therefore includes both the projected
channel edge and the causal acceptance-origin pivot.  The inherited natural
geometry may widen it further to a later confirmed five-minute counter swing.
The full-position plan is rejected if the first objective no longer offers one
gross R.
"""
from __future__ import annotations

from contracts_v5 import ScenarioSetup, StructureFamily
from domain import Side
from easychart_re1_delivery_channel_acceptance import (
    DeliveryChannelAcceptanceEngine,
)


CHANNEL_BREAK_WAVE_INVALIDATION_RULE = (
    "SOURCE_EXPLICIT:ACCEPTED_CHANNEL_BREAK_INVALIDATION_INCLUDES_THE_BREAKOUT_WAVE_ORIGIN_RATHER_THAN_A_ONE_TICK_INTRABAR_EDGE_ONLY"
)


class StructuralDeliveryChannelAcceptanceEngine(
    DeliveryChannelAcceptanceEngine,
):
    def _acceptance_stop(
        self,
        setup: ScenarioSetup,
        time_ns: int,
    ) -> float | None:
        members, lower, upper = self._projected_bounds(setup, time_ns)
        if not any(
            member.family is StructureFamily.CHANNEL
            for member in members
        ):
            return super()._acceptance_stop(setup, time_ns)
        boundary = (
            lower - self.tick_size
            if setup.side is Side.LONG
            else upper + self.tick_size
        )
        origin = setup.acceptance_origin
        if origin is None:
            self._inc("channel_acceptance_missing_wave_origin")
            return boundary
        structural = (
            origin.price - self.tick_size
            if setup.side is Side.LONG
            else origin.price + self.tick_size
        )
        stop = (
            min(boundary, structural)
            if setup.side is Side.LONG
            else max(boundary, structural)
        )
        self._inc("channel_acceptance_wave_origin_invalidation")
        return stop
