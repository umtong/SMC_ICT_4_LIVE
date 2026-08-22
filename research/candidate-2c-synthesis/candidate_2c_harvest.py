#!/usr/bin/env python3
"""Candidate 2c research-synthesis harvester.

This module keeps the strongest reusable mechanics from the causal auction lineage:
semantic liquidity owns the interaction, failed and accepted auctions share one event
grammar, OB/FVG geometry refines a first-return limit, the stop invalidates the event,
and the full position targets the first still-unconsumed opposing route that can pay at
least 1R before costs.

It also repairs a structural contradiction in the inherited implementation.  A pending
first-return order was cancelled by ``MAX_RESPONSE_BARS`` even though that constant was
not supplied by the v5 core and, more importantly, elapsed bars alone do not end the
opportunity.  Candidate 2c cancels an unfilled order only when the event is invalidated,
the target is spent, the first return has actually passed the declared entry, or the
causal opportunity expires.  Filled positions still end only at the immutable TP or SL.
"""
from __future__ import annotations

from typing import Any

import candidate_1k_harvest as synthesis

policy = synthesis.policy
core = synthesis.core
EPS = synthesis.EPS
POLICY = (
    "CANDIDATE_2C_RESEARCH_SYNTHESIS_SEMANTIC_LIQUIDITY_AUCTION_"
    "CAUSAL_FIRST_RETURN_PASSED_EVENT_INVALIDATION_EXACT_OPPOSING_ROUTE"
)


def causal_label_from_arm(
    data: Any,
    candidate: Any,
    arm: int,
    entry: float,
    stop: float,
    target: float,
    tick: float,
):
    """Label one immutable plan without an arbitrary response-bar cancellation.

    Future observations are used only to determine what happened to an order that was
    already available at ``arm``.  Merely waiting a fixed number of bars is not a causal
    invalidation.  Once the first return overlaps the decision zone, however, a close
    away from the zone without trading through the declared limit means that particular
    first-return price has been passed and the pending order is cancelled.
    """
    setup = candidate.setup
    side = str(setup.side)
    expiry = min(len(data) - 1, core._pending_expiry(candidate, candidate.source))
    bounded_arm = min(max(int(arm), 0), len(data) - 1)
    if arm >= expiry or not policy._pre_arm_alive(
        data,
        candidate,
        arm,
        entry,
        stop,
        target,
        tick,
    ):
        return policy._empty_label("ARM_NOT_AVAILABLE", data, bounded_arm)

    touched_zone = False
    for position in range(arm + 1, expiry + 1):
        row = data.iloc[position]
        invalidated = float(row.low) <= stop if side == "LONG" else float(row.high) >= stop
        target_spent = float(row.high) >= target if side == "LONG" else float(row.low) <= target
        traded = (
            float(row.low) <= entry - core.LIMIT_TRADE_THROUGH_TICKS * tick
            if side == "LONG"
            else float(row.high) >= entry + core.LIMIT_TRADE_THROUGH_TICKS * tick
        )
        overlaps = float(row.low) <= float(setup.upper) and float(row.high) >= float(setup.lower)

        if traded:
            if invalidated or target_spent:
                return policy._copy_label(
                    core._same_bar_stop_label(
                        data,
                        position,
                        arm,
                        entry,
                        stop,
                        target,
                        side,
                        tick,
                    )
                )
            return policy._copy_label(
                core._resolve_after_fill(
                    data,
                    position,
                    arm,
                    entry,
                    stop,
                    target,
                    side,
                    tick,
                )
            )
        if invalidated:
            return policy._empty_label("CANCELED_PRE_FILL_INVALIDATED", data, position)
        if target_spent:
            return policy._empty_label("CANCELED_PRE_FILL_TARGET_SPENT", data, position)

        if overlaps:
            touched_zone = True
            continue
        if touched_zone:
            close_away = (
                float(row.close) >= float(setup.upper)
                if side == "LONG"
                else float(row.close) <= float(setup.lower)
            )
            if close_away:
                return policy._empty_label("CANCELED_FIRST_RETURN_PASSED", data, position)

    return policy._empty_label("EXPIRED_UNFILLED", data, expiry)


# Candidate 1k's generator references the shared policy module at call time, so this
# single patch repairs all generated plans while preserving its exact-route logic.
policy.label_from_arm = causal_label_from_arm
core.POLICY = POLICY
core.generate_symbol = synthesis.generate_symbol

if __name__ == "__main__":
    core.main()
