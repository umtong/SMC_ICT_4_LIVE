"""Post-run structural path diagnostics for flow-response execution evidence.

This module is evaluation-only. It consumes already-completed NautilusTrader intents and closed
trades plus the historical ten-second frame which was replayed. It never emits a signal, changes an
order, sizes a position, or returns a score to the detector.

The diagnostics preserve continuous facts wherever possible:

* first structural touch of the original stop and completed-external target;
* maximum favorable and adverse price progress relative to their planned distances;
* whether the original target was reached only after the actual position was closed; and
* whether a stopped trade later reached its target inside the original maximum-hold horizon.

`TARGET_AFTER_INVALIDATION` is not labelled a winner. It only proves that the original direction and
target eventually occurred after the stated invalidation, which can motivate a separate examination
of entry timing versus stop logic. No threshold is fitted from PnL.
"""

from __future__ import annotations

from collections import Counter
from math import isfinite
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


_FIRST_TOUCH_VALUES = (
    "TARGET",
    "STOP",
    "AMBIGUOUS_SAME_BUCKET",
    "NONE_WITHIN_HORIZON",
)


def _direction_value(value: Any) -> int:
    text = str(value).upper()
    if text in {"LONG", "BUY", "1", "+1"}:
        return 1
    if text in {"SHORT", "SELL", "-1"}:
        return -1
    raise ValueError(f"unsupported trade direction: {value!r}")


def _numeric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = ("high", "low", "close")
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise KeyError(f"path diagnostic frame is missing columns: {missing}")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise TypeError("path diagnostic frame must use a timezone-aware DatetimeIndex")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("path diagnostic timestamps must be unique and increasing")
    result = frame.loc[:, required].copy()
    for column in required:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if not np.isfinite(result.to_numpy(dtype="float64")).all():
        raise ValueError("path diagnostic frame contains non-finite prices")
    if bool((result["high"] < result["low"]).any()):
        raise ValueError("path diagnostic frame contains high below low")
    return result


def _timestamp_ns(index: pd.DatetimeIndex) -> np.ndarray:
    return index.as_unit("ns").asi8


def _path_slice(
    frame: pd.DataFrame,
    *,
    after_time_ns: int,
    through_time_ns: int,
) -> pd.DataFrame:
    timestamps = _timestamp_ns(frame.index)
    start = int(np.searchsorted(timestamps, int(after_time_ns), side="right"))
    end = int(np.searchsorted(timestamps, int(through_time_ns), side="right"))
    return frame.iloc[start:end]


def _first_structural_touches(
    path: pd.DataFrame,
    *,
    direction: int,
    stop: float,
    target: float,
) -> dict[str, Any]:
    first_stop_time_ns: int | None = None
    first_target_time_ns: int | None = None
    ambiguous_time_ns: int | None = None
    for timestamp, row in path.iterrows():
        stop_hit = (
            float(row["low"]) <= stop
            if direction > 0
            else float(row["high"]) >= stop
        )
        target_hit = (
            float(row["high"]) >= target
            if direction > 0
            else float(row["low"]) <= target
        )
        timestamp_ns = int(timestamp.as_unit("ns").value)
        if stop_hit and first_stop_time_ns is None:
            first_stop_time_ns = timestamp_ns
        if target_hit and first_target_time_ns is None:
            first_target_time_ns = timestamp_ns
        if stop_hit and target_hit:
            ambiguous_time_ns = timestamp_ns
            break
        if first_stop_time_ns is not None or first_target_time_ns is not None:
            break

    if ambiguous_time_ns is not None:
        outcome = "AMBIGUOUS_SAME_BUCKET"
    elif first_target_time_ns is not None:
        outcome = "TARGET"
    elif first_stop_time_ns is not None:
        outcome = "STOP"
    else:
        outcome = "NONE_WITHIN_HORIZON"
    return {
        "structural_first_touch": outcome,
        "first_stop_time_ns": first_stop_time_ns,
        "first_target_time_ns": first_target_time_ns,
        "ambiguous_first_touch_time_ns": ambiguous_time_ns,
    }


def _extreme_progress(
    path: pd.DataFrame,
    *,
    direction: int,
    entry: float,
) -> dict[str, Any]:
    if path.empty:
        return {
            "maximum_favorable_price_progress": 0.0,
            "maximum_adverse_price_progress": 0.0,
            "maximum_favorable_time_ns": None,
            "maximum_adverse_time_ns": None,
        }
    if direction > 0:
        favorable_series = path["high"] - entry
        adverse_series = entry - path["low"]
    else:
        favorable_series = entry - path["low"]
        adverse_series = path["high"] - entry
    favorable_position = int(np.argmax(favorable_series.to_numpy(dtype="float64")))
    adverse_position = int(np.argmax(adverse_series.to_numpy(dtype="float64")))
    return {
        "maximum_favorable_price_progress": max(
            0.0,
            float(favorable_series.iloc[favorable_position]),
        ),
        "maximum_adverse_price_progress": max(
            0.0,
            float(adverse_series.iloc[adverse_position]),
        ),
        "maximum_favorable_time_ns": int(
            path.index[favorable_position].as_unit("ns").value
        ),
        "maximum_adverse_time_ns": int(
            path.index[adverse_position].as_unit("ns").value
        ),
    }


def diagnose_trade_path(
    *,
    frame: pd.DataFrame,
    intent: Mapping[str, Any],
    closed_trade: Mapping[str, Any],
    maximum_hold_minutes: int,
) -> dict[str, Any]:
    """Return exact post-run structural facts for one executed trade."""

    if maximum_hold_minutes <= 0:
        raise ValueError("maximum hold must be positive")
    values = _numeric_frame(frame)
    scenario_id = str(closed_trade.get("scenario_id"))
    fill_time_raw = intent.get("entry_fill_time_ns")
    fill_price_raw = intent.get("entry_fill_price")
    close_time_raw = closed_trade.get("position_close_time_ns")
    required_values = {
        "entry_fill_time_ns": fill_time_raw,
        "entry_fill_price": fill_price_raw,
        "position_close_time_ns": close_time_raw,
        "structural_stop": intent.get("structural_stop"),
        "external_target": intent.get("external_target"),
        "direction": intent.get("direction"),
    }
    missing = [name for name, value in required_values.items() if value is None]
    if missing:
        return {
            "scenario_id": scenario_id,
            "path_diagnostic_status": "MISSING_EXECUTION_FIELDS",
            "missing_fields": missing,
        }

    direction = _direction_value(required_values["direction"])
    entry = float(fill_price_raw)
    stop = float(required_values["structural_stop"])
    target = float(required_values["external_target"])
    fill_time_ns = int(fill_time_raw)
    close_time_ns = int(close_time_raw)
    if not all(isfinite(value) for value in (entry, stop, target)):
        raise ValueError(f"non-finite structural geometry for {scenario_id}")
    valid_geometry = stop < entry < target if direction > 0 else target < entry < stop
    if not valid_geometry:
        return {
            "scenario_id": scenario_id,
            "path_diagnostic_status": "INVALID_EXECUTED_GEOMETRY",
            "entry_fill_price": entry,
            "structural_stop": stop,
            "external_target": target,
        }
    if close_time_ns <= fill_time_ns:
        return {
            "scenario_id": scenario_id,
            "path_diagnostic_status": "NONPOSITIVE_HOLDING_TIME",
            "entry_fill_time_ns": fill_time_ns,
            "position_close_time_ns": close_time_ns,
        }

    hold_ns = int(maximum_hold_minutes) * 60 * 1_000_000_000
    horizon_time_ns = fill_time_ns + hold_ns
    path = _path_slice(
        values,
        after_time_ns=fill_time_ns,
        through_time_ns=horizon_time_ns,
    )
    actual_holding_path = _path_slice(
        values,
        after_time_ns=fill_time_ns,
        through_time_ns=close_time_ns,
    )
    after_close_path = _path_slice(
        values,
        after_time_ns=close_time_ns,
        through_time_ns=horizon_time_ns,
    )

    touches = _first_structural_touches(
        path,
        direction=direction,
        stop=stop,
        target=target,
    )
    actual_touches = _first_structural_touches(
        actual_holding_path,
        direction=direction,
        stop=stop,
        target=target,
    )
    after_close_touches = _first_structural_touches(
        after_close_path,
        direction=direction,
        stop=stop,
        target=target,
    )
    extremes = _extreme_progress(path, direction=direction, entry=entry)
    actual_extremes = _extreme_progress(
        actual_holding_path,
        direction=direction,
        entry=entry,
    )
    target_distance = abs(target - entry)
    stop_distance = abs(entry - stop)
    target_after_close = after_close_touches["first_target_time_ns"] is not None
    stop_after_close = after_close_touches["first_stop_time_ns"] is not None
    target_after_invalidation = (
        touches["structural_first_touch"] == "STOP"
        and touches["first_target_time_ns"] is None
        and target_after_close
    )
    # A first-touch stop loop exits before a later target can be recorded. Search the path strictly
    # after that stop to establish the exact structural diagnostic without relabelling the loss.
    if touches["structural_first_touch"] == "STOP" and touches["first_stop_time_ns"] is not None:
        after_stop = _path_slice(
            values,
            after_time_ns=int(touches["first_stop_time_ns"]),
            through_time_ns=horizon_time_ns,
        )
        after_stop_touch = _first_structural_touches(
            after_stop,
            direction=direction,
            stop=stop,
            target=target,
        )
        target_after_invalidation = after_stop_touch["first_target_time_ns"] is not None
        target_after_invalidation_time_ns = after_stop_touch["first_target_time_ns"]
    else:
        target_after_invalidation_time_ns = None

    return {
        "scenario_id": scenario_id,
        "path_diagnostic_status": "COMPLETE",
        "direction_value": direction,
        "entry_fill_time_ns": fill_time_ns,
        "position_close_time_ns": close_time_ns,
        "horizon_time_ns": horizon_time_ns,
        "entry_fill_price": entry,
        "structural_stop": stop,
        "external_target": target,
        "entry_to_stop_distance": stop_distance,
        "entry_to_target_distance": target_distance,
        "path_bars": int(len(path.index)),
        "actual_holding_path_bars": int(len(actual_holding_path.index)),
        **touches,
        "actual_holding_first_touch": actual_touches["structural_first_touch"],
        "target_reached_after_actual_close": target_after_close,
        "target_reached_after_actual_close_time_ns": after_close_touches[
            "first_target_time_ns"
        ],
        "stop_reached_after_actual_close": stop_after_close,
        "stop_reached_after_actual_close_time_ns": after_close_touches[
            "first_stop_time_ns"
        ],
        "target_reached_after_invalidation": target_after_invalidation,
        "target_reached_after_invalidation_time_ns": target_after_invalidation_time_ns,
        **extremes,
        "actual_holding_maximum_favorable_price_progress": actual_extremes[
            "maximum_favorable_price_progress"
        ],
        "actual_holding_maximum_adverse_price_progress": actual_extremes[
            "maximum_adverse_price_progress"
        ],
        "maximum_favorable_target_distance_fraction": (
            extremes["maximum_favorable_price_progress"] / target_distance
        ),
        "maximum_adverse_stop_distance_fraction": (
            extremes["maximum_adverse_price_progress"] / stop_distance
        ),
        "actual_holding_favorable_target_distance_fraction": (
            actual_extremes["maximum_favorable_price_progress"] / target_distance
        ),
        "actual_holding_adverse_stop_distance_fraction": (
            actual_extremes["maximum_adverse_price_progress"] / stop_distance
        ),
        "actual_close_reason": closed_trade.get("close_reason"),
        "actual_realized_pnl": float(closed_trade.get("realized_pnl", 0.0)),
    }


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
            }
        elif frame is None:
            diagnostic = {
                "scenario_id": scenario_id,
                "path_diagnostic_status": "SYMBOL_FRAME_NOT_FOUND",
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


def summarize_trade_path_diagnostics(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    diagnostics = [
        item.get("path_diagnostic", {})
        for item in records
        if isinstance(item.get("path_diagnostic", {}), Mapping)
    ]
    complete = [item for item in diagnostics if item.get("path_diagnostic_status") == "COMPLETE"]
    return {
        "records": len(diagnostics),
        "complete_records": len(complete),
        "status_counts": dict(
            sorted(Counter(str(item.get("path_diagnostic_status")) for item in diagnostics).items())
        ),
        "structural_first_touch_counts": dict(
            sorted(Counter(str(item.get("structural_first_touch")) for item in complete).items())
        ),
        "actual_holding_first_touch_counts": dict(
            sorted(Counter(str(item.get("actual_holding_first_touch")) for item in complete).items())
        ),
        "target_after_actual_close_count": sum(
            bool(item.get("target_reached_after_actual_close")) for item in complete
        ),
        "target_after_invalidation_count": sum(
            bool(item.get("target_reached_after_invalidation")) for item in complete
        ),
        "mean_actual_holding_favorable_target_fraction": (
            float(
                np.mean(
                    [
                        float(item["actual_holding_favorable_target_distance_fraction"])
                        for item in complete
                    ]
                )
            )
            if complete
            else 0.0
        ),
        "mean_actual_holding_adverse_stop_fraction": (
            float(
                np.mean(
                    [
                        float(item["actual_holding_adverse_stop_distance_fraction"])
                        for item in complete
                    ]
                )
            )
            if complete
            else 0.0
        ),
        "allowed_structural_first_touch_values": list(_FIRST_TOUCH_VALUES),
    }


__all__ = [
    "diagnose_trade_path",
    "enrich_closed_trade_records",
    "summarize_trade_path_diagnostics",
]
