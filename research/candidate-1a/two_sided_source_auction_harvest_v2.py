#!/usr/bin/env python3
"""Two-sided source auction with branch-correct invalidation and real micro-MSS response.

V1 removed OB/FVG from direction selection.  V2 repairs two remaining mismatches with
the discretionary logic:

* a failed-auction reversal is invalid only beyond the liquidity-sweep extreme, not at
  an arbitrary later micro candle;
* a first-return response must break the whole local control formed since the touch,
  not merely the immediately preceding one-minute candle.

Continuation keeps the defended micro-impulse stop.  Everything else is inherited from
candidate-1a and the v3-v7/episode stack.
"""
from __future__ import annotations

from typing import Any
import math

import pandas as pd

import two_sided_source_auction_harvest as v1

base = v1.base
core = v1.core
EPS = base.EPS


def _zone_with_branch_invalidation(
    data: pd.DataFrame,
    candidate: Any,
    arm: int,
    tick: float,
):
    zone = _ORIGINAL_ZONE(data, candidate, arm, tick)
    if zone is None:
        return None
    branch = str(candidate.event_meta.get("narrative_branch", ""))
    if branch == "FAILED_AUCTION_REVERSAL":
        source = getattr(candidate, "source", None)
        if source is None:
            return None
        event_stop = float(core._causal_stop(data, candidate, source, tick))
        valid = (
            event_stop < float(zone["lower"])
            if candidate.setup.side == "LONG"
            else event_stop > float(zone["upper"])
        )
        if not valid:
            return None
        zone = dict(zone)
        zone["micro_stop"] = float(zone["stop"])
        zone["stop"] = event_stop
        zone["stop_kind"] = "LIQUIDITY_SWEEP_EXTREME"
    else:
        zone = dict(zone)
        zone["micro_stop"] = float(zone["stop"])
        zone["stop_kind"] = "DEFENDED_MICRO_IMPULSE"
    return zone


def _strong_first_return_response(
    data: pd.DataFrame,
    zone: dict[str, Any],
    side: str,
    tick: float,
    expiry: int,
):
    sign = base._sign(side)
    touch: int | None = None
    extreme: float | None = None
    start = int(zone["creation_index"]) + 1
    end = min(len(data) - 2, expiry)
    for index in range(start, end + 1):
        row = data.iloc[index]
        invalidated = (
            float(row.low) <= float(zone["stop"])
            if side == "LONG"
            else float(row.high) >= float(zone["stop"])
        )
        if invalidated:
            return None
        overlaps = (
            float(row.low) <= float(zone["upper"])
            and float(row.high) >= float(zone["lower"])
        )
        if touch is None:
            if not overlaps:
                continue
            touch = index
            extreme = float(row.low if side == "LONG" else row.high)
            continue

        extreme = (
            min(float(extreme), float(row.low))
            if side == "LONG"
            else max(float(extreme), float(row.high))
        )
        if index - touch > base.MAX_RESPONSE_BARS:
            return None
        spent = (
            float(row.close) < float(zone["lower"]) - tick
            if side == "LONG"
            else float(row.close) > float(zone["upper"]) + tick
        )
        if spent:
            return None

        # A real micro market-structure shift must remove every opposing bar formed
        # after the first touch.  Breaking only the immediately preceding candle was
        # the dominant false-confirmation path in V1.
        local = data.iloc[touch:index]
        if local.empty:
            continue
        control_price = (
            float(local.high.max()) if side == "LONG" else float(local.low.min())
        )
        local_control = (
            float(row.close) >= control_price + tick
            if side == "LONG"
            else float(row.close) <= control_price - tick
        )
        aligned_body = sign * float(row.close - row.open) > 0.0
        closes_away = (
            float(row.close) >= float(zone["upper"])
            if side == "LONG"
            else float(row.close) <= float(zone["lower"])
        )
        q = max(base._finite(row.get("quote_volume")), EPS)
        signed = base._finite(row.get("signed_quote_flow"), float("nan"))
        if not math.isfinite(signed):
            signed = base._finite(row.get("delta_share")) * q
        flow_share = sign * signed / q
        price_progress = sign * float(row.close - row.open)
        initiative = flow_share > 0.0
        absorption = flow_share <= 0.0 and price_progress > 0.0
        if aligned_body and closes_away and local_control and (initiative or absorption):
            return {
                "touch_index": int(touch),
                "response_index": int(index),
                "retest_extreme": float(extreme),
                "response_kind": (
                    "ALIGNED_INITIATIVE"
                    if initiative
                    else "ADVERSE_FLOW_ABSORBED"
                ),
                "response_flow_share_signed": float(flow_share),
                "response_body_ratio": base._finite(row.get("body_ratio")),
                "response_range_ratio": base._finite(row.get("range_ratio")),
                "response_activity_ratio": base._finite(row.get("activity_ratio")),
                "return_wait_minutes": float(touch - int(zone["creation_index"])),
                "response_delay_minutes": float(index - touch),
                "response_control_distance_bps": abs(
                    float(row.close) - control_price
                ) / max(abs(float(row.close)), EPS) * 10_000.0,
            }
    return None


def generate_symbol(symbol, data, levels, metadata, trading_start):
    original_zone = base._control_transfer_zone
    original_response = base._first_return_response
    try:
        base._control_transfer_zone = _zone_with_branch_invalidation
        base._first_return_response = _strong_first_return_response
        frame, counts = v1.generate_symbol(
            symbol, data, levels, metadata, trading_start
        )
    finally:
        base._control_transfer_zone = original_zone
        base._first_return_response = original_response
    if not frame.empty:
        frame = frame.copy()
        frame["stop_kind"] = frame.family.map(
            {
                "FAILED_AUCTION_REVERSAL": "LIQUIDITY_SWEEP_EXTREME",
                "ACCEPTED_AUCTION_CONTINUATION": "DEFENDED_MICRO_IMPULSE",
            }
        )
        frame["response_confirmation"] = "TOUCH_SEQUENCE_MICRO_MSS"
    return frame, counts


_ORIGINAL_ZONE = base._control_transfer_zone
core.POLICY = (
    "SEMANTIC_SOURCE_CAUSAL_RECLAIM_OR_ACCEPTANCE_THEN_FRESH_MICRO_"
    "CONTROL_TRANSFER_THEN_FIRST_RETURN_SEQUENCE_MSS_AND_PRICE_FLOW_"
    "DEFENSE_THEN_NEXT_MINUTE_ENTRY_BRANCH_CORRECT_INVALIDATION_TO_"
    "FIRST_OPPOSING_UNCONSUMED_ROUTE"
)
core.generate_symbol = generate_symbol

if __name__ == "__main__":
    core.main()
