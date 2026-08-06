#!/usr/bin/env python3
"""Causal market-state audit for candidate-04 V18c.

This module is deliberately not an execution simulator. It reconstructs the
state transitions already emitted by the frozen NautilusTrader strategy and
answers two diagnostic questions from completed one-minute bars:

1. After a liquidity rejection, did a causal reversal structure shift appear
   before the rejection was invalidated?
2. After rejection failure/acceptance, how much directional excursion followed
   in the continuation and reversal directions?

No output from this module is accepted as performance evidence. Any rule derived
from it must be implemented as a Strategy and re-run through NautilusTrader.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

import nt_backtest as base
from nt_auction_excess_strategy import weighted_location
from nt_liquidity_strategy import net_r_at_price


HORIZONS = (5, 15, 30, 60, 120, 180)
CONTEXT_HORIZONS = (15, 30, 60, 120, 240, 480, 720)
TERMINAL_EVENTS = {
    "REJECTION_SUCCEEDED_NO_CONTINUATION",
    "REJECTION_PROBE_EXPIRED",
    "HIGH_IMPACT_OR_STALE_ACCEPTANCE_SKIPPED",
    "LOW_IMPACT_CAUSAL_TARGET_CONFIRMED",
    "NO_CAUSAL_LIQUIDITY_TARGET",
    "LOW_IMPACT_ACCEPTANCE_OCCUPIED",
}
ACCEPTANCE_EVENTS = {
    "HIGH_IMPACT_OR_STALE_ACCEPTANCE_SKIPPED",
    "LOW_IMPACT_CAUSAL_TARGET_CONFIRMED",
    "NO_CAUSAL_LIQUIDITY_TARGET",
    "LOW_IMPACT_ACCEPTANCE_OCCUPIED",
}


def _as_float(value: Any, default: float = float("nan")) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _atr(frame: pd.DataFrame, index: int, period: int = 30) -> float:
    if index < period:
        return float("nan")
    selected = frame.iloc[index - period : index + 1]
    previous_close = selected["close"].shift(1)
    true_range = pd.concat(
        [
            selected["high"] - selected["low"],
            (selected["high"] - previous_close).abs(),
            (selected["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    values = true_range.iloc[1:]
    return float(values.mean()) if len(values) == period else float("nan")


def _volume_burst(frame: pd.DataFrame, index: int, window: int = 120) -> float:
    if index <= 0:
        return 0.0
    start = max(0, index - window)
    history = frame.iloc[start:index]["volume"]
    median = float(history.median()) if not history.empty else 0.0
    return float(frame.iloc[index]["volume"]) / median if median > 0.0 else 0.0


def _close_location(row: pd.Series, side: int) -> float:
    span = max(float(row["high"] - row["low"]), 1e-12)
    if side > 0:
        return float(row["close"] - row["low"]) / span
    return float(row["high"] - row["close"]) / span


def _fair_value(frame: pd.DataFrame, end_exclusive: int, window: int) -> float:
    if end_exclusive < window:
        return float("nan")
    selected = frame.iloc[end_exclusive - window : end_exclusive]
    typical = ((selected["high"] + selected["low"] + selected["close"]) / 3.0).tolist()
    volume = selected["volume"].tolist()
    mean, _ = weighted_location(typical, volume)
    return float(mean)


def _index_at(frame: pd.DataFrame, ts_event: int) -> int:
    timestamp = pd.Timestamp(ts_event, unit="ns", tz="UTC")
    location = frame.index.get_indexer([timestamp], method="pad")[0]
    if location < 0:
        raise RuntimeError(f"event precedes data: {timestamp}")
    return int(location)


def _context_features(
    frame: pd.DataFrame,
    index: int,
    side: int,
    value_window: int,
) -> dict[str, float]:
    atr = _atr(frame, index)
    if not math.isfinite(atr) or atr <= 0.0:
        return {}
    close = float(frame.iloc[index]["close"])
    result: dict[str, float] = {"atr": atr}
    for horizon in CONTEXT_HORIZONS:
        if index < horizon:
            continue
        selected = frame.iloc[index - horizon : index + 1]
        previous_close = float(frame.iloc[index - horizon]["close"])
        high = float(selected["high"].max())
        low = float(selected["low"].min())
        changes = selected["close"].diff().abs().iloc[1:]
        path = float(changes.sum())
        span = max(high - low, 1e-12)
        result[f"directional_displacement_{horizon}m_atr"] = (
            side * (close - previous_close) / atr
        )
        result[f"auction_efficiency_{horizon}m"] = (
            abs(close - previous_close) / path if path > 0.0 else 0.0
        )
        result[f"directional_range_location_{horizon}m"] = (
            (close - low) / span if side > 0 else (high - close) / span
        )
        result[f"path_{horizon}m_atr"] = path / atr
        result[f"range_{horizon}m_atr"] = span / atr

    current_fair = _fair_value(frame, index, value_window)
    for horizon in (15, 30, 60):
        older_fair = _fair_value(frame, index - horizon, value_window)
        if math.isfinite(current_fair) and math.isfinite(older_fair):
            result[f"directional_fair_value_slope_{horizon}m_atr"] = (
                side * (current_fair - older_fair) / atr
            )
    return result


@dataclass(slots=True)
class Probe:
    scale: str
    armed_event: dict[str, Any]
    armed_index: int
    reversal_side: int
    continuation_side: int
    sweep_extreme: float
    reclaimed_boundary: float
    fair_value: float
    prior_high: float
    prior_low: float
    value_window: int
    liquidity_window: int
    resolution_event: dict[str, Any] | None = None


def _infer_probe(frame: pd.DataFrame, event: dict[str, Any]) -> Probe:
    details = event["details"]
    index = _index_at(frame, int(event["ts_event"]))
    row = frame.iloc[index]
    prior_high = _as_float(details.get("prior_high"))
    prior_low = _as_float(details.get("prior_low"))
    low_swept = float(row["low"]) < prior_low and float(row["close"]) > prior_low
    high_swept = float(row["high"]) > prior_high and float(row["close"]) < prior_high
    if low_swept == high_swept:
        raise RuntimeError(
            f"cannot infer sweep side at {frame.index[index]}: "
            f"low_swept={low_swept} high_swept={high_swept}"
        )
    reversal_side = 1 if low_swept else -1
    return Probe(
        scale=str(details.get("liquidity_scale")),
        armed_event=event,
        armed_index=index,
        reversal_side=reversal_side,
        continuation_side=-reversal_side,
        sweep_extreme=float(row["low"] if low_swept else row["high"]),
        reclaimed_boundary=prior_low if low_swept else prior_high,
        fair_value=_as_float(details.get("fair_value")),
        prior_high=prior_high,
        prior_low=prior_low,
        value_window=int(details.get("scale_value_window", 240)),
        liquidity_window=int(details.get("scale_liquidity_window", 30)),
    )


def _match_probes(frame: pd.DataFrame, events: list[dict[str, Any]]) -> list[Probe]:
    active: dict[str, Probe] = {}
    completed: list[Probe] = []
    ordered = sorted(events, key=lambda item: int(item["ts_event"]))
    for event in ordered:
        event_type = str(event.get("event_type"))
        details = event.get("details") or {}
        scale = details.get("liquidity_scale")
        if event_type == "REJECTION_PROBE_ARMED":
            probe = _infer_probe(frame, event)
            if probe.scale in active:
                completed.append(active.pop(probe.scale))
            active[probe.scale] = probe
            continue
        if event_type in TERMINAL_EVENTS and scale in active:
            probe = active.pop(str(scale))
            probe.resolution_event = event
            completed.append(probe)
    completed.extend(active.values())
    return sorted(completed, key=lambda item: item.armed_index)


def _resolution_name(probe: Probe) -> str:
    if probe.resolution_event is None:
        return "OPEN_AT_END"
    return str(probe.resolution_event.get("event_type"))


def _scan_excursion(
    frame: pd.DataFrame,
    start_index: int,
    side: int,
    entry: float,
    atr: float,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for horizon in HORIZONS:
        end = min(len(frame), start_index + horizon + 1)
        selected = frame.iloc[start_index + 1 : end]
        if selected.empty:
            result[f"mfe_{horizon}m_atr"] = 0.0
            result[f"mae_{horizon}m_atr"] = 0.0
            continue
        if side > 0:
            favorable = float(selected["high"].max()) - entry
            adverse = entry - float(selected["low"].min())
        else:
            favorable = entry - float(selected["low"].min())
            adverse = float(selected["high"].max()) - entry
        result[f"mfe_{horizon}m_atr"] = favorable / atr
        result[f"mae_{horizon}m_atr"] = adverse / atr
    return result


def _first_touch(
    frame: pd.DataFrame,
    start_index: int,
    side: int,
    target: float,
    stop: float,
    max_bars: int = 180,
) -> tuple[str, int | None]:
    end = min(len(frame), start_index + max_bars + 1)
    for offset, (_, row) in enumerate(frame.iloc[start_index + 1 : end].iterrows(), start=1):
        target_hit = float(row["high"]) >= target if side > 0 else float(row["low"]) <= target
        stop_hit = float(row["low"]) <= stop if side > 0 else float(row["high"]) >= stop
        if target_hit and stop_hit:
            return "AMBIGUOUS_SAME_BAR", offset
        if target_hit:
            return "TARGET_FIRST", offset
        if stop_hit:
            return "STOP_FIRST", offset
    return "NEITHER", None


def _rejection_confirmation(
    frame: pd.DataFrame,
    probe: Probe,
    cost_rate: float,
    stop_buffer_atr: float,
) -> dict[str, Any] | None:
    index = probe.armed_index
    if index < 5:
        return None
    pre = frame.iloc[index - 5 : index]
    structure = (
        float(pre["high"].max())
        if probe.reversal_side > 0
        else float(pre["low"].min())
    )
    last = min(len(frame) - 1, index + 4)
    for candidate_index in range(index + 1, last + 1):
        row = frame.iloc[candidate_index]
        invalid = (
            float(row["close"]) < probe.sweep_extreme
            if probe.reversal_side > 0
            else float(row["close"]) > probe.sweep_extreme
        )
        if invalid:
            return None
        fair_hit = (
            float(row["high"]) >= probe.fair_value
            if probe.reversal_side > 0
            else float(row["low"]) <= probe.fair_value
        )
        if fair_hit:
            return None
        broken = (
            float(row["close"]) > structure
            if probe.reversal_side > 0
            else float(row["close"]) < structure
        )
        if not broken:
            continue
        atr = _atr(frame, candidate_index)
        if not math.isfinite(atr) or atr <= 0.0:
            return None
        body = probe.reversal_side * float(row["close"] - row["open"]) / atr
        volume_burst = _volume_burst(frame, candidate_index)
        close_location = _close_location(row, probe.reversal_side)
        if not (body >= 0.45 and volume_burst >= 1.10 and close_location >= 0.65):
            return None

        entry = float(row["close"])
        stop = probe.sweep_extreme - probe.reversal_side * stop_buffer_atr * atr
        price_loss = probe.reversal_side * (entry - stop)
        planned_loss = price_loss + cost_rate * (entry + stop)
        target_net_r = net_r_at_price(
            entry,
            probe.fair_value,
            probe.reversal_side,
            planned_loss,
            cost_rate,
        )
        if not math.isfinite(target_net_r) or target_net_r < 1.20:
            return {
                "confirmation_index": candidate_index,
                "confirmation_ts": int(frame.index[candidate_index].value),
                "qualified": False,
                "reason": "FAIR_VALUE_TOO_CLOSE",
                "target_net_r": target_net_r,
                "entry": entry,
                "stop": stop,
                "target": probe.fair_value,
                "body_atr": body,
                "volume_burst": volume_burst,
                "close_location": close_location,
                "structure": structure,
            }
        outcome, bars = _first_touch(
            frame,
            candidate_index,
            probe.reversal_side,
            probe.fair_value,
            stop,
        )
        return {
            "confirmation_index": candidate_index,
            "confirmation_ts": int(frame.index[candidate_index].value),
            "qualified": True,
            "reason": "CONFIRMED",
            "target_net_r": target_net_r,
            "entry": entry,
            "stop": stop,
            "target": probe.fair_value,
            "body_atr": body,
            "volume_burst": volume_burst,
            "close_location": close_location,
            "structure": structure,
            "first_touch": outcome,
            "bars_to_touch": bars,
        }
    return None


def _probe_row(
    frame: pd.DataFrame,
    probe: Probe,
    cost_rate: float,
    stop_buffer_atr: float,
) -> dict[str, Any]:
    arm = probe.armed_event
    details = dict(arm.get("details") or {})
    arm_row = frame.iloc[probe.armed_index]
    record: dict[str, Any] = {
        "scale": probe.scale,
        "armed_ts": int(arm["ts_event"]),
        "armed_time": frame.index[probe.armed_index].isoformat(),
        "resolution": _resolution_name(probe),
        "reversal_side": probe.reversal_side,
        "continuation_side": probe.continuation_side,
        "sweep_extreme": probe.sweep_extreme,
        "reclaimed_boundary": probe.reclaimed_boundary,
        "fair_value": probe.fair_value,
        "prior_high": probe.prior_high,
        "prior_low": probe.prior_low,
        "arm_close": float(arm_row["close"]),
    }
    for key, value in details.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            record[f"arm_{key}"] = value
    record.update(
        {
            f"reversal_{key}": value
            for key, value in _context_features(
                frame,
                probe.armed_index,
                probe.reversal_side,
                probe.value_window,
            ).items()
        }
    )
    confirmation = _rejection_confirmation(
        frame,
        probe,
        cost_rate,
        stop_buffer_atr,
    )
    if confirmation is not None:
        for key, value in confirmation.items():
            record[f"rejection_confirmation_{key}"] = value

    if probe.resolution_event is not None:
        resolution = probe.resolution_event
        resolution_index = _index_at(frame, int(resolution["ts_event"]))
        resolution_row = frame.iloc[resolution_index]
        resolution_details = dict(resolution.get("details") or {})
        record["resolution_ts"] = int(resolution["ts_event"])
        record["resolution_time"] = frame.index[resolution_index].isoformat()
        record["resolution_close"] = float(resolution_row["close"])
        record["resolution_delay_bars"] = resolution_index - probe.armed_index
        for key, value in resolution_details.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                record[f"resolution_{key}"] = value

        if _resolution_name(probe) in ACCEPTANCE_EVENTS:
            atr = _atr(frame, resolution_index)
            if math.isfinite(atr) and atr > 0.0:
                entry = float(resolution_row["close"])
                record.update(
                    {
                        f"continuation_{key}": value
                        for key, value in _context_features(
                            frame,
                            resolution_index,
                            probe.continuation_side,
                            probe.value_window,
                        ).items()
                    }
                )
                record.update(
                    {
                        f"continuation_{key}": value
                        for key, value in _scan_excursion(
                            frame,
                            resolution_index,
                            probe.continuation_side,
                            entry,
                            atr,
                        ).items()
                    }
                )
                record.update(
                    {
                        f"acceptance_reversal_{key}": value
                        for key, value in _scan_excursion(
                            frame,
                            resolution_index,
                            probe.reversal_side,
                            entry,
                            atr,
                        ).items()
                    }
                )
    return record


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cost-bps-each-side", type=float, default=7.5)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.10)
    args = parser.parse_args()

    frame, _ = base.load_week(
        date.fromisoformat(args.build_start),
        date.fromisoformat(args.build_end),
        args.cache.resolve(),
    )
    events = json.loads(args.events.read_text(encoding="utf-8"))
    probes = _match_probes(frame, events)
    cost_rate = args.cost_bps_each_side / 10_000.0
    rows = [
        _probe_row(frame, probe, cost_rate, args.stop_buffer_atr)
        for probe in probes
    ]
    args.output.mkdir(parents=True, exist_ok=True)
    _write_rows(args.output / "probe_outcomes.csv", rows)
    (args.output / "probe_outcomes.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )

    summary: dict[str, Any] = {
        "probes": len(rows),
        "resolutions": {},
        "rejection_structure_confirmations": 0,
        "qualified_rejection_structure_confirmations": 0,
        "qualified_target_first": 0,
        "qualified_stop_first": 0,
        "qualified_ambiguous": 0,
    }
    for row in rows:
        resolution = str(row["resolution"])
        summary["resolutions"][resolution] = int(
            summary["resolutions"].get(resolution, 0)
        ) + 1
        if "rejection_confirmation_confirmation_ts" in row:
            summary["rejection_structure_confirmations"] += 1
            if bool(row.get("rejection_confirmation_qualified")):
                summary["qualified_rejection_structure_confirmations"] += 1
                outcome = str(row.get("rejection_confirmation_first_touch"))
                if outcome == "TARGET_FIRST":
                    summary["qualified_target_first"] += 1
                elif outcome == "STOP_FIRST":
                    summary["qualified_stop_first"] += 1
                elif outcome == "AMBIGUOUS_SAME_BAR":
                    summary["qualified_ambiguous"] += 1
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
