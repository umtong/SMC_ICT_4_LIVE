"""Strict horizon and continuity wrapper for post-run structural path diagnostics.

The original diagnostic computes correct structural touch and MFE/MAE facts for the supplied frame.
This revision first proves that the supplied official ten-second frame contains every bucket from the
first post-fill bucket through the complete configured maximum-hold horizon. Missing tail data or a
missing ten-second bucket is an evidence-contract failure, never a strategy result.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from flow_response_trade_path_diagnostics import (
    diagnose_trade_path as _diagnose_v1,
    summarize_trade_path_diagnostics,
)


TEN_SECOND_NS = 10_000_000_000
MAXIMUM_TEN_SECOND_GAP_NS = 11_000_000_000
DIAGNOSTIC_REVISION = "POST_RUN_STRUCTURAL_PATH_COMPLETE_HORIZON_V2"


def _preflight_horizon(
    *,
    frame: pd.DataFrame,
    intent: Mapping[str, Any],
    closed_trade: Mapping[str, Any],
    maximum_hold_minutes: int,
) -> dict[str, Any] | None:
    scenario_id = str(closed_trade.get("scenario_id"))
    if maximum_hold_minutes <= 0:
        raise ValueError("maximum hold must be positive")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise TypeError("path diagnostic frame must use a timezone-aware DatetimeIndex")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("path diagnostic timestamps must be unique and increasing")
    if frame.empty:
        return {
            "scenario_id": scenario_id,
            "path_diagnostic_status": "EMPTY_REPLAY_FRAME",
            "diagnostic_revision": DIAGNOSTIC_REVISION,
        }

    fill_time_raw = intent.get("entry_fill_time_ns")
    if fill_time_raw is None:
        return None  # v1 reports the complete missing execution-field set.
    fill_time_ns = int(fill_time_raw)
    horizon_time_ns = fill_time_ns + int(maximum_hold_minutes) * 60 * 1_000_000_000
    timestamps = frame.index.as_unit("ns").asi8
    first_post_fill = int(np.searchsorted(timestamps, fill_time_ns, side="right"))
    if first_post_fill >= len(timestamps):
        return {
            "scenario_id": scenario_id,
            "path_diagnostic_status": "MISSING_POST_ENTRY_PATH_START",
            "diagnostic_revision": DIAGNOSTIC_REVISION,
            "entry_fill_time_ns": fill_time_ns,
            "required_first_post_fill_time_ns": fill_time_ns + TEN_SECOND_NS,
            "available_last_time_ns": int(timestamps[-1]),
        }
    first_post_fill_time_ns = int(timestamps[first_post_fill])
    if first_post_fill_time_ns - fill_time_ns > MAXIMUM_TEN_SECOND_GAP_NS:
        return {
            "scenario_id": scenario_id,
            "path_diagnostic_status": "MISSING_POST_ENTRY_PATH_START",
            "diagnostic_revision": DIAGNOSTIC_REVISION,
            "entry_fill_time_ns": fill_time_ns,
            "first_post_fill_time_ns": first_post_fill_time_ns,
            "gap_ns": first_post_fill_time_ns - fill_time_ns,
        }

    available_last_time_ns = int(timestamps[-1])
    if available_last_time_ns < horizon_time_ns:
        return {
            "scenario_id": scenario_id,
            "path_diagnostic_status": "INCOMPLETE_MAX_HOLD_HORIZON",
            "diagnostic_revision": DIAGNOSTIC_REVISION,
            "entry_fill_time_ns": fill_time_ns,
            "required_horizon_time_ns": horizon_time_ns,
            "available_last_time_ns": available_last_time_ns,
            "missing_tail_ns": horizon_time_ns - available_last_time_ns,
        }

    end = int(np.searchsorted(timestamps, horizon_time_ns, side="right"))
    horizon_timestamps = timestamps[first_post_fill:end]
    if horizon_timestamps.size == 0:
        return {
            "scenario_id": scenario_id,
            "path_diagnostic_status": "EMPTY_POST_ENTRY_HORIZON",
            "diagnostic_revision": DIAGNOSTIC_REVISION,
            "entry_fill_time_ns": fill_time_ns,
            "required_horizon_time_ns": horizon_time_ns,
        }
    deltas = np.diff(horizon_timestamps)
    gap_positions = np.flatnonzero(deltas != TEN_SECOND_NS)
    if gap_positions.size:
        first_gap = int(gap_positions[0])
        return {
            "scenario_id": scenario_id,
            "path_diagnostic_status": "NONCONTIGUOUS_TEN_SECOND_HORIZON",
            "diagnostic_revision": DIAGNOSTIC_REVISION,
            "entry_fill_time_ns": fill_time_ns,
            "required_horizon_time_ns": horizon_time_ns,
            "gap_count": int(gap_positions.size),
            "maximum_gap_ns": int(deltas[gap_positions].max()),
            "first_gap_start_time_ns": int(horizon_timestamps[first_gap]),
            "first_gap_end_time_ns": int(horizon_timestamps[first_gap + 1]),
        }
    return None


def diagnose_trade_path(
    *,
    frame: pd.DataFrame,
    intent: Mapping[str, Any],
    closed_trade: Mapping[str, Any],
    maximum_hold_minutes: int,
) -> dict[str, Any]:
    preflight = _preflight_horizon(
        frame=frame,
        intent=intent,
        closed_trade=closed_trade,
        maximum_hold_minutes=maximum_hold_minutes,
    )
    if preflight is not None:
        return preflight
    result = _diagnose_v1(
        frame=frame,
        intent=intent,
        closed_trade=closed_trade,
        maximum_hold_minutes=maximum_hold_minutes,
    )
    return {**result, "diagnostic_revision": DIAGNOSTIC_REVISION}


def enrich_closed_trade_records(
    *,
    records: Sequence[Mapping[str, Any]],
    intents: Sequence[Mapping[str, Any]],
    frames_by_symbol: Mapping[str, pd.DataFrame],
    maximum_hold_minutes: int,
) -> list[dict[str, Any]]:
    intent_by_scenario = {str(item.get("scenario_id")): item for item in intents}
    enriched: list[dict[str, Any]] = []
    for record in records:
        value = dict(record)
        scenario_id = str(value.get("scenario_id"))
        intent = intent_by_scenario.get(scenario_id)
        symbol = str(value.get("symbol"))
        frame = frames_by_symbol.get(symbol)
        if intent is None:
            diagnostic = {
                "scenario_id": scenario_id,
                "path_diagnostic_status": "INTENT_NOT_FOUND",
                "diagnostic_revision": DIAGNOSTIC_REVISION,
            }
        elif frame is None:
            diagnostic = {
                "scenario_id": scenario_id,
                "path_diagnostic_status": "SYMBOL_FRAME_NOT_FOUND",
                "diagnostic_revision": DIAGNOSTIC_REVISION,
                "symbol": symbol,
            }
        else:
            diagnostic = diagnose_trade_path(
                frame=frame,
                intent=intent,
                closed_trade=value,
                maximum_hold_minutes=maximum_hold_minutes,
            )
        value["path_diagnostic"] = diagnostic
        enriched.append(value)
    return enriched


__all__ = [
    "DIAGNOSTIC_REVISION",
    "TEN_SECOND_NS",
    "diagnose_trade_path",
    "enrich_closed_trade_records",
    "summarize_trade_path_diagnostics",
]
