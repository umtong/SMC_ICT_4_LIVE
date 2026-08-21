"""Structural invalidation for accepted channel breaks.

A channel is a moving boundary, not the complete breakout thesis.  A stop only
one tick beyond its projected edge converts ordinary retest noise into a full
loss and manufactures large planned R from an unrealistically small denominator.
The supplied trend/channel material places accepted-break invalidation at the
wave which created the break, while the already completed retest candle must
also remain inside the initial risk geometry.

For channel acceptance the immutable stop therefore lies beyond all observable
failure facts: the projected channel edge, the first detached retest wick and,
when available, the causal acceptance-origin pivot.  The inherited natural
geometry may widen it further to a later confirmed five-minute counter swing.
The full-position plan is rejected if the true first objective no longer offers
one gross R.
"""
from __future__ import annotations

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, StructureFamily
from domain import Side
from easychart_re1_delivery_channel_acceptance import (
    DeliveryChannelAcceptanceEngine,
)


CHANNEL_BREAK_WAVE_INVALIDATION_RULE = (
    "SOURCE_EXPLICIT:"
    "ACCEPTED_CHANNEL_BREAK_INVALIDATION_INCLUDES_THE_BREAKOUT_WAVE_ORIGIN_AND_COMPLETED_RETEST_WICK_NOT_A_ONE_TICK_EDGE_ONLY"
)
if CHANNEL_BREAK_WAVE_INVALIDATION_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CHANNEL_BREAK_WAVE_INVALIDATION_RULE,)


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
        if not self.trigger_detector.bars:
            raise RuntimeError("channel acceptance stop requested before retest bar")
        current = self.trigger_detector.bars[-1]
        if current.ts_close_ns != time_ns:
            raise RuntimeError("channel acceptance stop must use current completed retest")

        boundary = (
            lower - self.tick_size
            if setup.side is Side.LONG
            else upper + self.tick_size
        )
        retest = (
            current.low - self.tick_size
            if setup.side is Side.LONG
            else current.high + self.tick_size
        )
        candidates = [boundary, retest]
        origin = setup.acceptance_origin
        if origin is None:
            self._inc("channel_acceptance_missing_wave_origin")
        else:
            candidates.append(
                origin.price - self.tick_size
                if setup.side is Side.LONG
                else origin.price + self.tick_size
            )
        stop = (
            min(candidates)
            if setup.side is Side.LONG
            else max(candidates)
        )
        if setup.side is Side.LONG and not stop < current.low:
            raise RuntimeError("long channel acceptance stop is not beyond retest wick")
        if setup.side is Side.SHORT and not stop > current.high:
            raise RuntimeError("short channel acceptance stop is not beyond retest wick")
        self._inc("channel_acceptance_structural_invalidation")
        self._trace(
            "channel_acceptance_structural_invalidation_selected",
            time_ns,
            setup,
            projected_boundary_stop=boundary,
            retest_wick_stop=retest,
            origin_pivot_id=None if origin is None else origin.pivot_id,
            origin_price=None if origin is None else origin.price,
            stop=stop,
            rule_provenance=CHANNEL_BREAK_WAVE_INVALIDATION_RULE,
        )
        return stop
