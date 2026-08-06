"""Causal passive-depth confirmation for failed-acceptance trap entries.

This module is a pattern detector only. It reads the normalized Binance Vision
bookDepth series and evaluates whether passive liquidity changed in favor of the
already completed price/flow trap scenario. It never creates orders, sizes
positions, or computes PnL.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import csv
from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class DepthObservation:
    ts_ns: int
    bid_near: float
    ask_near: float


@dataclass(frozen=True, slots=True)
class DepthGateResult:
    passed: bool
    reason: str
    metrics: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _DepthSeries:
    observations: tuple[DepthObservation, ...]
    timestamps: tuple[int, ...]


@lru_cache(maxsize=12)
def _load_depth_series(path_text: str) -> _DepthSeries:
    path = Path(path_text).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"depth series does not exist: {path}")

    rows: list[DepthObservation] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"ts_ns", "bid_near", "ask_near"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"depth series missing columns: {sorted(missing)}")
        for raw in reader:
            try:
                ts_ns = int(raw["ts_ns"])
                bid_near = float(raw["bid_near"])
                ask_near = float(raw["ask_near"])
            except (KeyError, TypeError, ValueError):
                continue
            if ts_ns <= 0 or not all(
                math.isfinite(value) and value > 0.0
                for value in (bid_near, ask_near)
            ):
                continue
            rows.append(DepthObservation(ts_ns, bid_near, ask_near))

    rows.sort(key=lambda row: row.ts_ns)
    deduplicated: list[DepthObservation] = []
    for row in rows:
        if deduplicated and row.ts_ns == deduplicated[-1].ts_ns:
            deduplicated[-1] = row
        else:
            deduplicated.append(row)
    if not deduplicated:
        raise ValueError(f"depth series contains no valid observations: {path}")
    observations = tuple(deduplicated)
    return _DepthSeries(observations, tuple(row.ts_ns for row in observations))


def _window(series: _DepthSeries, start_ns: int, end_ns: int) -> tuple[DepthObservation, ...]:
    if end_ns < start_ns:
        return ()
    left = bisect_left(series.timestamps, start_ns)
    right = bisect_right(series.timestamps, end_ns)
    return series.observations[left:right]


def _side_values(
    rows: Sequence[DepthObservation],
    direction: str,
) -> tuple[list[float], list[float]]:
    if direction == "LONG":
        return [row.bid_near for row in rows], [row.ask_near for row in rows]
    if direction == "SHORT":
        return [row.ask_near for row in rows], [row.bid_near for row in rows]
    raise ValueError(f"unsupported trap direction: {direction}")


def _failure(reason: str, **metrics: Any) -> DepthGateResult:
    return DepthGateResult(False, reason, metrics)


def evaluate_failed_acceptance_depth(
    original_signal: Any,
    trap_signal: Any,
    snapshot: Any,
    params: Mapping[str, Any],
) -> DepthGateResult:
    """Evaluate depth only through the completed failed-defense bar.

    The anchor is the timestamp of the original SAC signal. The pre-window ends
    at that timestamp. The event window starts strictly after it and ends at the
    completed bar that invalidated SAC and armed the opposite FAT scenario.
    Therefore no observation after the entry decision can enter the gate.
    """

    path = str(params.get("depth_series_path", "")).strip()
    anchor_ts_ns = int(getattr(original_signal, "observed_ts_ns"))
    decision_ts_ns = int(snapshot.observation.ts_ns)
    direction = str(getattr(trap_signal, "direction")).upper()
    base_metrics: dict[str, Any] = {
        "depth_series_path": path,
        "anchor_ts_ns": anchor_ts_ns,
        "decision_ts_ns": decision_ts_ns,
        "trap_direction": direction,
    }
    if not path:
        return _failure("DEPTH_SERIES_PATH_MISSING", **base_metrics)
    if decision_ts_ns <= anchor_ts_ns:
        return _failure("DEPTH_EVENT_WINDOW_NOT_CAUSAL", **base_metrics)

    pre_window_seconds = int(params.get("fatr_depth_pre_window_seconds", 120))
    max_age_seconds = int(params.get("fatr_depth_max_age_seconds", 90))
    min_pre_records = int(params.get("fatr_depth_min_pre_records", 2))
    min_event_records = int(params.get("fatr_depth_min_event_records", 2))
    final_records = int(params.get("fatr_depth_final_records", 1))
    minimum_recovery = float(params.get("fatr_depth_min_recovery_fraction", 0.50))
    if min(pre_window_seconds, max_age_seconds, min_pre_records, min_event_records, final_records) <= 0:
        return _failure("DEPTH_GATE_CONFIGURATION_INVALID", **base_metrics)
    if not 0.0 <= minimum_recovery <= 1.0:
        return _failure("DEPTH_GATE_CONFIGURATION_INVALID", **base_metrics)

    try:
        series = _load_depth_series(path)
    except (OSError, ValueError) as exc:
        return _failure(
            "DEPTH_SERIES_UNAVAILABLE_OR_INVALID",
            **base_metrics,
            error=f"{type(exc).__name__}: {exc}",
        )

    second_ns = 1_000_000_000
    pre_rows = _window(
        series,
        anchor_ts_ns - pre_window_seconds * second_ns,
        anchor_ts_ns,
    )
    event_rows = _window(series, anchor_ts_ns + 1, decision_ts_ns)
    metrics = {
        **base_metrics,
        "pre_window_seconds": pre_window_seconds,
        "pre_records": len(pre_rows),
        "event_records": len(event_rows),
        "minimum_recovery_fraction": minimum_recovery,
    }
    if len(pre_rows) < min_pre_records:
        return _failure("DEPTH_PRE_WINDOW_INSUFFICIENT", **metrics)
    if len(event_rows) < min_event_records:
        return _failure("DEPTH_EVENT_WINDOW_INSUFFICIENT", **metrics)

    pre_age_seconds = (anchor_ts_ns - pre_rows[-1].ts_ns) / second_ns
    event_age_seconds = (decision_ts_ns - event_rows[-1].ts_ns) / second_ns
    metrics.update(
        {
            "pre_latest_age_seconds": pre_age_seconds,
            "event_latest_age_seconds": event_age_seconds,
        },
    )
    if pre_age_seconds > max_age_seconds:
        return _failure("DEPTH_PRE_WINDOW_STALE", **metrics)
    if event_age_seconds > max_age_seconds:
        return _failure("DEPTH_EVENT_WINDOW_STALE", **metrics)

    pre_source, pre_target = _side_values(pre_rows, direction)
    event_source, event_target = _side_values(event_rows, direction)
    source_pre = float(median(pre_source))
    target_pre = float(median(pre_target))
    final_count = min(final_records, len(event_rows))
    source_final = float(median(event_source[-final_count:]))
    target_final = float(median(event_target[-final_count:]))
    source_trough = float(min(event_source))

    if min(source_pre, target_pre, source_final, target_final, source_trough) <= 0.0:
        return _failure("DEPTH_NON_POSITIVE_MEASUREMENT", **metrics)

    depletion = source_pre - source_trough
    if depletion > 0.0:
        recovery_fraction = (source_final - source_trough) / depletion
    else:
        recovery_fraction = 1.0 if source_final >= source_pre else 0.0
    source_log_change = math.log(source_final / source_pre)
    target_log_change = math.log(target_final / target_pre)
    path_asymmetry = source_log_change - target_log_change
    source_supported = source_final >= source_pre or recovery_fraction >= minimum_recovery
    path_open = path_asymmetry > 0.0

    metrics.update(
        {
            "source_side": "BID" if direction == "LONG" else "ASK",
            "target_side": "ASK" if direction == "LONG" else "BID",
            "source_pre": source_pre,
            "source_trough": source_trough,
            "source_final": source_final,
            "target_pre": target_pre,
            "target_final": target_final,
            "source_recovery_fraction": recovery_fraction,
            "source_log_change": source_log_change,
            "target_log_change": target_log_change,
            "path_asymmetry": path_asymmetry,
            "source_supported": source_supported,
            "target_path_open": path_open,
            "last_observation_ts_ns": event_rows[-1].ts_ns,
        },
    )
    if not source_supported:
        return _failure("TRAP_SOURCE_SIDE_NOT_REPLENISHED", **metrics)
    if not path_open:
        return _failure("TRAP_TARGET_PATH_NOT_RELATIVELY_OPEN", **metrics)
    return DepthGateResult(True, "SYNCHRONOUS_DEPTH_RESILIENCY_CONFIRMED", metrics)
