#!/usr/bin/env python3
"""Arm first-return limit orders only after observable directional commitment.

The economic hypothesis is preserved: a semantic failed/accepted auction can offer
an excellent first-return price.  The causal correction is not to discard that
hypothesis, but to prevent an immediate weak return from being treated as evidence
of control transfer.  Every candidate exists at departure.  A limit order becomes
live only after completed bars show sufficient excursion, outside dwell and path
efficiency.  A return before commitment cancels the setup.  Once filled, the only
exits are the predeclared take-profit and stop-loss.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any

import numpy as np
import pandas as pd

import departure_first_return_harvest_fixed as fixed

core = fixed.core
EPS = 1e-12


@dataclass(frozen=True, slots=True)
class CommitmentBarrierLabel:
    fill_state: str
    outcome: str
    fill_index: int | None
    fill_time_ns: int | None
    resolution_index: int | None
    resolution_time_ns: int | None
    order_terminal_index: int
    order_terminal_time_ns: int
    entry_wait_minutes: float | None
    holding_minutes: float | None
    actual_entry: float | None
    actual_target_net_r: float | None
    actual_stop_net_r: float | None
    actual_gross_rr: float | None
    net_r: float | None
    mfe_r: float | None
    mae_r: float | None = None
    armed: bool = False
    arm_index: int | None = None
    arm_time_ns: int | None = None
    arm_to_fill_minutes: float | None = None
    commitment_progress_r: float | None = None
    commitment_progress_atr: float | None = None
    commitment_path_efficiency: float | None = None
    commitment_outside_closes: int | None = None
    commitment_flow_share_signed: float | None = None
    commitment_activity_ratio: float | None = None


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _thresholds(candidate) -> tuple[float, float, int, float]:
    branch = str(candidate.event_meta["narrative_branch"])
    if branch == "FAILED_AUCTION_REVERSAL":
        # A reclaim is not enough.  Require a visible inward auction before buying
        # or selling its first return, while preserving the early price advantage.
        return 0.40, 0.25, 2, 0.45
    # Accepted auctions need stronger proof because a one-bar overshoot is common.
    return 0.60, 0.40, 3, 0.55


def _metrics(
    data: pd.DataFrame,
    departure: int,
    current: int,
    entry: float,
    stop: float,
    side: str,
    atr: float,
    outside_closes: int,
) -> dict[str, float | int]:
    sign = 1.0 if side == "LONG" else -1.0
    close = float(data.iloc[current].close)
    risk = max(abs(float(entry) - float(stop)), EPS)
    progress = sign * (close - float(entry))
    path = data.close.iloc[departure:current + 1].to_numpy(float)
    travel = float(np.abs(np.diff(path)).sum()) if len(path) > 1 else 0.0
    efficiency = sign * (path[-1] - path[0]) / max(travel, EPS)
    segment = data.iloc[departure:current + 1]
    quote = pd.to_numeric(segment.get("quote_volume", pd.Series(index=segment.index, dtype=float)), errors="coerce").fillna(0.0)
    if "signed_quote_flow" in segment:
        signed = pd.to_numeric(segment.signed_quote_flow, errors="coerce").fillna(0.0)
    elif "delta_share" in segment:
        signed = pd.to_numeric(segment.delta_share, errors="coerce").fillna(0.0) * quote
    else:
        signed = pd.Series(0.0, index=segment.index)
    prior_quote = pd.to_numeric(
        data.quote_volume.iloc[max(0, departure - 60):departure], errors="coerce"
    ).median() if "quote_volume" in data else float("nan")
    return {
        "commitment_progress_r": progress / risk,
        "commitment_progress_atr": progress / max(atr, EPS),
        "commitment_path_efficiency": efficiency,
        "commitment_outside_closes": int(outside_closes),
        "commitment_flow_share_signed": sign * float(signed.sum()) / max(float(quote.sum()), EPS),
        "commitment_activity_ratio": float(quote.mean()) / max(_finite(prior_quote, float(quote.mean())), EPS),
    }


def _cancel(
    state: str,
    data: pd.DataFrame,
    position: int,
    *,
    armed: bool = False,
    arm_index: int | None = None,
    metrics: dict[str, Any] | None = None,
) -> CommitmentBarrierLabel:
    timestamp = int(data.index[position].value)
    values = metrics or {}
    return CommitmentBarrierLabel(
        fill_state=state,
        outcome="UNFILLED",
        fill_index=None,
        fill_time_ns=None,
        resolution_index=None,
        resolution_time_ns=None,
        order_terminal_index=position,
        order_terminal_time_ns=timestamp,
        entry_wait_minutes=None,
        holding_minutes=None,
        actual_entry=None,
        actual_target_net_r=None,
        actual_stop_net_r=None,
        actual_gross_rr=None,
        net_r=None,
        mfe_r=None,
        mae_r=None,
        armed=armed,
        arm_index=arm_index,
        arm_time_ns=int(data.index[arm_index].value) if arm_index is not None else None,
        **values,
    )


def _decorate(label, arm_index: int, metrics: dict[str, Any]):
    fill_index = getattr(label, "fill_index", None)
    return replace(
        label,
        armed=True,
        arm_index=arm_index,
        arm_time_ns=int(label_time := 0) if False else int(_DATA_INDEX[arm_index].value),
        arm_to_fill_minutes=float(fill_index - arm_index) if fill_index is not None else None,
        **metrics,
    )


# _decorate needs the current index but helper labels are generated by the core
# module.  This module-local value is set only during one synchronous label call.
_DATA_INDEX: pd.DatetimeIndex


def commitment_label_pending(data, candidate, entry, stop, target, tick):
    global _DATA_INDEX
    _DATA_INDEX = data.index
    setup = candidate.setup
    departure = int(candidate.departure_index)
    side = str(setup.side)
    sign = 1.0 if side == "LONG" else -1.0
    expiry = min(len(data) - 1, core._pending_expiry(candidate, candidate.source))
    atr = max(core._atr_price(data, departure), EPS)
    minimum_r, minimum_atr, minimum_dwell, minimum_efficiency = _thresholds(candidate)
    zone_lower, zone_upper = float(setup.lower), float(setup.upper)
    previous_close = float(data.iloc[departure].close)
    travel = 0.0
    outside_closes = 0
    armed = False
    arm_index: int | None = None
    arm_metrics: dict[str, Any] | None = None
    touch_index: int | None = None

    for position in range(departure + 1, expiry + 1):
        row = data.iloc[position]
        close = float(row.close)
        travel += abs(close - previous_close)
        previous_close = close
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
                return _cancel("CANCELED_PRE_ARM_INVALIDATED", data, position)
            if target_spent:
                return _cancel("CANCELED_PRE_ARM_TARGET_SPENT", data, position)
            # A first return which begins before observable commitment is not the
            # skilled-trader setup; do not wait for a later, different return.
            if overlaps_zone or traded_through:
                return _cancel("CANCELED_RETURN_BEFORE_COMMITMENT", data, position)
            values = _metrics(data, departure, position, entry, stop, side, atr, outside_closes)
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
        if invalidated:
            return _cancel(
                "CANCELED_PRE_FILL_INVALIDATED", data, position,
                armed=True, arm_index=arm_index, metrics=arm_metrics,
            )
        if target_spent:
            return _cancel(
                "CANCELED_PRE_FILL_TARGET_SPENT", data, position,
                armed=True, arm_index=arm_index, metrics=arm_metrics,
            )
        if traded_through:
            if invalidated or target_spent:
                label = core._same_bar_stop_label(
                    data, position, arm_index, entry, stop, target, side, tick
                )
            else:
                label = core._resolve_after_fill(
                    data, position, arm_index, entry, stop, target, side, tick
                )
            return _decorate(label, arm_index, arm_metrics)
        if touch_index is None and overlaps_zone:
            touch_index = position
        elif touch_index is not None:
            close_away = close >= zone_upper if side == "LONG" else close <= zone_lower
            if close_away or position - touch_index > core.MAX_RESPONSE_BARS:
                return _cancel(
                    "CANCELED_FIRST_RETURN_PASSED", data, position,
                    armed=True, arm_index=arm_index, metrics=arm_metrics,
                )

    if armed and arm_index is not None and arm_metrics is not None:
        return _cancel(
            "EXPIRED_ARMED_UNFILLED", data, expiry,
            armed=True, arm_index=arm_index, metrics=arm_metrics,
        )
    return _cancel("EXPIRED_WITHOUT_COMMITMENT", data, expiry)


core.BarrierLabel = CommitmentBarrierLabel
core.label_pending = commitment_label_pending

_BASE_GENERATE = core.generate_symbol


def generate_symbol(symbol, data, levels, metadata, trading_start):
    frame, counts = _BASE_GENERATE(symbol, data, levels, metadata, trading_start)
    if frame.empty:
        return frame, counts
    frame["departure_order_time_ns"] = frame.order_time_ns
    armed = frame.armed.fillna(False).astype(bool)
    frame.loc[armed, "order_time_ns"] = pd.to_numeric(
        frame.loc[armed, "arm_time_ns"], errors="coerce"
    )
    decision_end = getattr(fixed, "_DECISION_END_NS", None)
    if decision_end is not None:
        frame = frame[
            (~frame.armed.fillna(False).astype(bool))
            | pd.to_numeric(frame.order_time_ns, errors="coerce").lt(decision_end)
        ].copy()
    counts = dict(counts)
    counts["armed_plans"] = int(frame.armed.fillna(False).sum())
    counts["armed_states"] = int(frame.loc[frame.armed.fillna(False), "state_id"].nunique())
    counts["plans"] = int(len(frame))
    return frame, counts


core.generate_symbol = generate_symbol

if __name__ == "__main__":
    core.main()
