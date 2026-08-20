#!/usr/bin/env python3
"""Executable commitment harvester with conservative post-arm fill ordering."""
from __future__ import annotations

import pandas as pd

import commitment_first_return_harvest as policy

core = policy.core


def commitment_label_pending(data, candidate, entry, stop, target, tick):
    policy._DATA_INDEX = data.index
    setup = candidate.setup
    departure = int(candidate.departure_index)
    side = str(setup.side)
    expiry = min(len(data) - 1, core._pending_expiry(candidate, candidate.source))
    atr = max(core._atr_price(data, departure), policy.EPS)
    minimum_r, minimum_atr, minimum_dwell, minimum_efficiency = policy._thresholds(candidate)
    zone_lower, zone_upper = float(setup.lower), float(setup.upper)
    outside_closes = 0
    armed = False
    arm_index = None
    arm_metrics = None
    touch_index = None

    for position in range(departure + 1, expiry + 1):
        row = data.iloc[position]
        close = float(row.close)
        invalidated = float(row.low) <= float(stop) if side == "LONG" else float(row.high) >= float(stop)
        target_spent = float(row.high) >= float(target) if side == "LONG" else float(row.low) <= float(target)
        traded_through = (
            float(row.low) <= float(entry) - core.LIMIT_TRADE_THROUGH_TICKS * tick
            if side == "LONG"
            else float(row.high) >= float(entry) + core.LIMIT_TRADE_THROUGH_TICKS * tick
        )
        overlaps_zone = float(row.low) <= zone_upper and float(row.high) >= zone_lower
        outside = close > zone_upper + tick if side == "LONG" else close < zone_lower - tick
        outside_closes = outside_closes + 1 if outside else 0

        if not armed:
            if invalidated:
                return policy._cancel("CANCELED_PRE_ARM_INVALIDATED", data, position)
            if target_spent:
                return policy._cancel("CANCELED_PRE_ARM_TARGET_SPENT", data, position)
            if overlaps_zone or traded_through:
                return policy._cancel("CANCELED_RETURN_BEFORE_COMMITMENT", data, position)
            values = policy._metrics(
                data, departure, position, entry, stop, side, atr, outside_closes
            )
            if (
                float(values["commitment_progress_r"]) >= minimum_r
                and float(values["commitment_progress_atr"]) >= minimum_atr
                and int(values["commitment_outside_closes"]) >= minimum_dwell
                and float(values["commitment_path_efficiency"]) >= minimum_efficiency
            ):
                armed = True
                arm_index = position
                arm_metrics = values
            continue

        assert arm_index is not None and arm_metrics is not None
        # After the order is live, a trade-through and barrier print in the same
        # one-minute bar is a possible filled loss, never an unfilled cancellation.
        if traded_through:
            if invalidated or target_spent:
                label = core._same_bar_stop_label(
                    data, position, arm_index, entry, stop, target, side, tick
                )
            else:
                label = core._resolve_after_fill(
                    data, position, arm_index, entry, stop, target, side, tick
                )
            return policy._decorate(label, arm_index, arm_metrics)
        if invalidated:
            return policy._cancel(
                "CANCELED_PRE_FILL_INVALIDATED", data, position,
                armed=True, arm_index=arm_index, metrics=arm_metrics,
            )
        if target_spent:
            return policy._cancel(
                "CANCELED_PRE_FILL_TARGET_SPENT", data, position,
                armed=True, arm_index=arm_index, metrics=arm_metrics,
            )
        if touch_index is None and overlaps_zone:
            touch_index = position
        elif touch_index is not None:
            close_away = close >= zone_upper if side == "LONG" else close <= zone_lower
            if close_away or position - touch_index > core.MAX_RESPONSE_BARS:
                return policy._cancel(
                    "CANCELED_FIRST_RETURN_PASSED", data, position,
                    armed=True, arm_index=arm_index, metrics=arm_metrics,
                )

    if armed and arm_index is not None and arm_metrics is not None:
        return policy._cancel(
            "EXPIRED_ARMED_UNFILLED", data, expiry,
            armed=True, arm_index=arm_index, metrics=arm_metrics,
        )
    return policy._cancel("EXPIRED_WITHOUT_COMMITMENT", data, expiry)


core.label_pending = commitment_label_pending

if __name__ == "__main__":
    core.main()
